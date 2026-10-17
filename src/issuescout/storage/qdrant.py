"""Qdrant access for code chunks (SRS 6.1; FR-IDX-13, FR-RET-01; NFR-SEC-06).

Two rules are enforced here and nowhere else:

* **One search function.** Every vector query goes through :func:`search_code`, which
  requires a repository ID and an index version and always adds them as filters. There is
  no code path that searches without them, so one repository's code cannot appear in
  another's results. ``tests/security`` fails if any other module queries Qdrant directly.
* **Deterministic point IDs.** Indexing the same commit twice upserts the same points
  instead of adding new ones.
"""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# QdrantClient is re-exported so other modules can type against it without importing the
# qdrant_client package themselves (tests/security enforces that only storage/ does).
from qdrant_client import QdrantClient as QdrantClient
from qdrant_client import models

from issuescout.indexing.models import Chunk, ChunkKind
from issuescout.indexing.sparse import SparseVector, payload_embedding_text
from issuescout.settings import Settings

CODE_COLLECTION = "code_chunks"
DENSE = "dense"
SPARSE = "sparse"
# Fixed namespace for point IDs. Changing it would orphan every stored point.
_ID_NAMESPACE = uuid.UUID("6f1c2f0e-5d0a-4a52-9d5e-0f5c7e0b1a11")

# Payload fields that searches filter on (SRS 6.1, "Indexed" = yes).
_KEYWORD_INDEXES = ("path", "language", "kind")
_INTEGER_INDEXES = ("installation_id", "index_version")


def point_id(repo_id: int, index_version: int, chunk: Chunk) -> str:
    """UUIDv5 of repository, index version, path, symbol and content hash (FR-IDX-13).

    Oversized chunks that were split share path and symbol but differ in text, so each
    window still gets its own ID. Two byte-identical definitions of one name in one file
    collapse into a single point, which loses nothing a search could return.
    """
    key = f"{repo_id}|{index_version}|{chunk.path}|{chunk.symbol}|{chunk.content_hash}"
    return str(uuid.uuid5(_ID_NAMESPACE, key))


def make_client(settings: Settings) -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection(
    client: QdrantClient, dense_dim: int | None, name: str = CODE_COLLECTION
) -> None:
    """Create the collection and its payload indexes if missing; verify the size if present.

    ``dense_dim=None`` makes a sparse-only collection, for baselines that never embed.
    """
    if client.collection_exists(name):
        params = client.get_collection(name).config.params.vectors
        existing = params[DENSE].size if isinstance(params, dict) and DENSE in params else None
        if dense_dim is not None and existing != dense_dim:
            raise ValueError(
                f"collection {name!r} has dense size {existing}, expected {dense_dim}; "
                "the embedding model changed, so re-index into a new collection"
            )
        return
    dense_config = (
        {DENSE: models.VectorParams(size=dense_dim, distance=models.Distance.COSINE)}
        if dense_dim is not None
        else {}
    )
    client.create_collection(
        name,
        vectors_config=dense_config,
        # No IDF modifier: Qdrant would compute IDF over the whole collection, so one
        # repository's scores would depend on every other repository (and on write history).
        # IDF is applied to the query instead, from per-repository statistics (termstats.py).
        sparse_vectors_config={SPARSE: models.SparseVectorParams()},
    )
    # repo_id is the tenant key. It stays an integer as in SRS 6.1, so it gets an exact-match
    # integer index; Qdrant's tenant co-location flag exists only on keyword indexes, which
    # is an optimisation to revisit if the P4.4 load test needs it.
    client.create_payload_index(
        name,
        "repo_id",
        models.IntegerIndexParams(type=models.IntegerIndexType.INTEGER, lookup=True, range=False),
    )
    for field in _KEYWORD_INDEXES:
        client.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD)
    for field in _INTEGER_INDEXES:
        client.create_payload_index(name, field, models.PayloadSchemaType.INTEGER)
    client.create_payload_index(name, "is_test", models.PayloadSchemaType.BOOL)


def chunk_payload(
    chunk: Chunk, repo_id: int, installation_id: int, commit_sha: str, index_version: int
) -> dict[str, object]:
    return {
        "repo_id": repo_id,
        "installation_id": installation_id,
        "commit_sha": commit_sha,
        "path": chunk.path,
        "language": chunk.language,
        "kind": chunk.kind,
        "symbol": chunk.symbol,
        "parent_symbol": chunk.parent_symbol,
        "signature": chunk.signature,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "content_hash": chunk.content_hash,
        "text": chunk.text,
        "token_count": chunk.token_count,
        "is_test": chunk.is_test,
        "index_version": index_version,
    }


def upsert_chunks(
    client: QdrantClient,
    chunks: Sequence[Chunk],
    dense: Sequence[Sequence[float]] | None,
    sparse: Sequence[SparseVector],
    *,
    repo_id: int,
    installation_id: int,
    commit_sha: str,
    index_version: int,
    collection: str = CODE_COLLECTION,
    batch_size: int = 128,
) -> int:
    """Write chunks with both vectors. Returns the number of points sent.

    Re-running with the same inputs overwrites the same points (FR-IDX-13, TC-06).
    """
    if len(chunks) != len(sparse) or (dense is not None and len(dense) != len(chunks)):
        raise ValueError("chunks, dense and sparse vectors must have equal length")
    points = []
    for i, chunk in enumerate(chunks):
        vector: dict[str, models.Vector] = {
            SPARSE: models.SparseVector(indices=list(sparse[i][0]), values=list(sparse[i][1]))
        }
        if dense is not None:
            vector[DENSE] = [float(x) for x in dense[i]]
        points.append(
            models.PointStruct(
                id=point_id(repo_id, index_version, chunk),
                vector=vector,
                payload=chunk_payload(chunk, repo_id, installation_id, commit_sha, index_version),
            )
        )
    for start in range(0, len(points), batch_size):
        client.upsert(collection, points[start : start + batch_size], wait=True)
    return len(points)


@dataclass(frozen=True)
class Hit:
    score: float
    path: str
    symbol: str
    kind: ChunkKind
    start_line: int
    end_line: int
    text: str
    is_test: bool
    repo_id: int
    index_version: int


def _scope(repo_id: int, index_version: int, exclude_tests: bool) -> models.Filter:
    must: list[models.Condition] = [
        models.FieldCondition(key="repo_id", match=models.MatchValue(value=repo_id)),
        models.FieldCondition(key="index_version", match=models.MatchValue(value=index_version)),
    ]
    if exclude_tests:
        must.append(models.FieldCondition(key="is_test", match=models.MatchValue(value=False)))
    return models.Filter(must=must)


def search_code(
    client: QdrantClient,
    *,
    repo_id: int,
    index_version: int,
    dense: Sequence[float] | None = None,
    sparse: SparseVector | None = None,
    limit: int = 50,
    candidates: int | None = None,
    exclude_tests: bool = False,
    collection: str = CODE_COLLECTION,
) -> list[Hit]:
    """The only way to search code chunks (NFR-SEC-06).

    Pass ``dense``, ``sparse`` or both. With both, the two searches run as prefetches
    of ``candidates`` chunks each (default ``limit``) and are merged by reciprocal rank
    fusion in one Qdrant query (FR-RET-02), which needs no score calibration between the
    two lists. ``repo_id`` and ``index_version`` have no defaults and are applied as a
    filter to every stage, so a search can never cross repositories or index versions.
    """
    if dense is None and sparse is None:
        raise ValueError("pass dense, sparse or both")
    use_sparse = sparse is not None and bool(sparse[0])  # no usable terms: nothing to match
    if dense is None and not use_sparse:
        return []
    query_filter = _scope(repo_id, index_version, exclude_tests)

    def dense_query() -> list[float]:
        assert dense is not None
        return [float(x) for x in dense]

    def sparse_query() -> models.SparseVector:
        assert sparse is not None
        return models.SparseVector(indices=list(sparse[0]), values=list(sparse[1]))

    if dense is not None and use_sparse:
        depth = candidates or limit
        response = client.query_points(
            collection,
            prefetch=[
                models.Prefetch(query=dense_query(), using=DENSE, filter=query_filter, limit=depth),
                models.Prefetch(
                    query=sparse_query(), using=SPARSE, filter=query_filter, limit=depth
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
    elif dense is not None:
        response = client.query_points(
            collection,
            query=dense_query(),
            using=DENSE,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
    else:
        response = client.query_points(
            collection,
            query=sparse_query(),
            using=SPARSE,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
    hits = [_hit(p.score, p.payload or {}) for p in response.points]
    # Equal scores are common (similar functions; RRF ranks tie often) and Qdrant returns
    # ties in an order that depends on write history. Break them by location so the same
    # index and query always give the same ranking (FR-RET-12).
    return sorted(hits, key=lambda h: (-h.score, h.path, h.start_line, h.symbol))


def _hit(score: float, payload: dict[str, object]) -> Hit:
    return Hit(
        score=score,
        path=str(payload["path"]),
        symbol=str(payload["symbol"]),
        kind=payload["kind"],  # type: ignore[arg-type]
        start_line=int(payload["start_line"]),  # type: ignore[call-overload]
        end_line=int(payload["end_line"]),  # type: ignore[call-overload]
        text=str(payload["text"]),
        is_test=bool(payload["is_test"]),
        repo_id=int(payload["repo_id"]),  # type: ignore[call-overload]
        index_version=int(payload["index_version"]),  # type: ignore[call-overload]
    )


def count_points(
    client: QdrantClient,
    *,
    repo_id: int,
    index_version: int | None = None,
    collection: str = CODE_COLLECTION,
) -> int:
    must: list[models.Condition] = [
        models.FieldCondition(key="repo_id", match=models.MatchValue(value=repo_id))
    ]
    if index_version is not None:
        must.append(
            models.FieldCondition(key="index_version", match=models.MatchValue(value=index_version))
        )
    return client.count(collection, count_filter=models.Filter(must=must), exact=True).count


@dataclass(frozen=True)
class StoredPoint:
    id: str
    text: str  # the chunk's embedding text, rebuilt from its payload


def _stored(point: models.Record) -> StoredPoint:
    return StoredPoint(str(point.id), payload_embedding_text(point.payload or {}))


_TEXT_FIELDS = ["path", "symbol", "text"]


def existing_point_ids(
    client: QdrantClient, ids: Sequence[str], collection: str = CODE_COLLECTION
) -> set[str]:
    """Which of ``ids`` are already in the collection (used to count each chunk once)."""
    if not ids or not client.collection_exists(collection):
        return set()
    found: set[str] = set()
    for start in range(0, len(ids), 500):
        batch = list(ids[start : start + 500])
        found.update(
            str(p.id)
            for p in client.retrieve(collection, batch, with_payload=False, with_vectors=False)
        )
    return found


def fetch_points_by_ids(
    client: QdrantClient, ids: Sequence[str], collection: str = CODE_COLLECTION
) -> list[StoredPoint]:
    out: list[StoredPoint] = []
    for start in range(0, len(ids), 500):
        batch = list(ids[start : start + 500])
        records = client.retrieve(collection, batch, with_payload=_TEXT_FIELDS, with_vectors=False)
        out.extend(_stored(r) for r in records)
    return out


def fetch_repo_points(
    client: QdrantClient,
    *,
    repo_id: int,
    index_version: int,
    paths: Sequence[str] | None = None,
    collection: str = CODE_COLLECTION,
) -> list[StoredPoint]:
    """Text of every chunk of a repository version, or only of the given files."""
    if not client.collection_exists(collection):
        return []
    path_batches: list[Sequence[str] | None] = (
        [paths[i : i + 500] for i in range(0, len(paths), 500)] if paths is not None else [None]
    )
    out: list[StoredPoint] = []
    for batch in path_batches:
        scope = _scope(repo_id, index_version, exclude_tests=False)
        if batch is not None:
            scope.must = [
                *(scope.must or []),
                models.FieldCondition(key="path", match=models.MatchAny(any=list(batch))),
            ]
        offset = None
        while True:
            records, offset = client.scroll(
                collection,
                scroll_filter=scope,
                limit=1000,
                offset=offset,
                with_payload=_TEXT_FIELDS,
                with_vectors=False,
            )
            out.extend(_stored(r) for r in records)
            if offset is None:
                break
    return out


def path_has_points(
    client: QdrantClient,
    *,
    repo_id: int,
    index_version: int,
    path: str,
    collection: str = CODE_COLLECTION,
) -> bool:
    """True if the index holds at least one chunk of ``path`` for this repository version."""
    scope = _scope(repo_id, index_version, exclude_tests=False)
    scope.must = [
        *(scope.must or []),
        models.FieldCondition(key="path", match=models.MatchValue(value=path)),
    ]
    return client.count(collection, count_filter=scope, exact=True).count > 0


def delete_paths(
    client: QdrantClient,
    *,
    repo_id: int,
    index_version: int,
    paths: Sequence[str],
    collection: str = CODE_COLLECTION,
    on_delete: Callable[[list[StoredPoint]], None] | None = None,
) -> None:
    """Delete every chunk of the given files (deleted or modified in a sync, FR-SYN-03).

    ``on_delete`` receives the text of the points about to be removed, so the caller can
    update the repository's term statistics before they are gone.
    """
    if on_delete is not None:
        doomed = fetch_repo_points(
            client, repo_id=repo_id, index_version=index_version, paths=paths, collection=collection
        )
        on_delete(doomed)
    for start in range(0, len(paths), 500):
        batch = list(paths[start : start + 500])
        scope = _scope(repo_id, index_version, exclude_tests=False)
        scope.must = [
            *(scope.must or []),
            models.FieldCondition(key="path", match=models.MatchAny(any=batch)),
        ]
        client.delete(collection, points_selector=models.FilterSelector(filter=scope), wait=True)


def delete_stale_points(
    client: QdrantClient,
    *,
    repo_id: int,
    index_version: int,
    keep_ids: set[str],
    collection: str = CODE_COLLECTION,
    on_delete: Callable[[list[StoredPoint]], None] | None = None,
) -> int:
    """Delete this repository version's points whose IDs are not in ``keep_ids``.

    Run after an upsert so chunks that no longer exist (deleted or edited code) disappear
    and the index equals the working tree. Returns the number removed.
    """
    scope = _scope(repo_id, index_version, exclude_tests=False)
    stale: list[models.ExtendedPointId] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection, scroll_filter=scope, limit=1000, offset=offset, with_payload=False
        )
        stale.extend(p.id for p in points if str(p.id) not in keep_ids)
        if offset is None:
            break
    for start in range(0, len(stale), 1000):
        batch = stale[start : start + 1000]
        if on_delete is not None:
            on_delete(fetch_points_by_ids(client, [str(i) for i in batch], collection))
        client.delete(collection, points_selector=models.PointIdsList(points=batch), wait=True)
    return len(stale)


def delete_repository(
    client: QdrantClient, *, repo_id: int, collection: str = CODE_COLLECTION
) -> None:
    """Remove every point of one repository (uninstall, FR-APP-10)."""
    client.delete(
        collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="repo_id", match=models.MatchValue(value=repo_id))]
            )
        ),
        wait=True,
    )
