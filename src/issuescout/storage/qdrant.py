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
from collections.abc import Sequence
from dataclasses import dataclass

from qdrant_client import QdrantClient, models

from issuescout.indexing.models import Chunk, ChunkKind
from issuescout.indexing.sparse import SparseVector
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


def ensure_collection(client: QdrantClient, dense_dim: int, name: str = CODE_COLLECTION) -> None:
    """Create the collection and its payload indexes if missing; verify the size if present."""
    if client.collection_exists(name):
        params = client.get_collection(name).config.params.vectors
        existing = params[DENSE].size if isinstance(params, dict) else None
        if existing != dense_dim:
            raise ValueError(
                f"collection {name!r} has dense size {existing}, expected {dense_dim}; "
                "the embedding model changed, so re-index into a new collection"
            )
        return
    client.create_collection(
        name,
        vectors_config={
            DENSE: models.VectorParams(size=dense_dim, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
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
    dense: Sequence[Sequence[float]],
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
    if not (len(chunks) == len(dense) == len(sparse)):
        raise ValueError("chunks, dense and sparse vectors must have equal length")
    points = [
        models.PointStruct(
            id=point_id(repo_id, index_version, chunk),
            vector={
                DENSE: [float(x) for x in d],
                SPARSE: models.SparseVector(indices=list(s[0]), values=list(s[1])),
            },
            payload=chunk_payload(chunk, repo_id, installation_id, commit_sha, index_version),
        )
        for chunk, d, s in zip(chunks, dense, sparse, strict=True)
    ]
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
    exclude_tests: bool = False,
    collection: str = CODE_COLLECTION,
) -> list[Hit]:
    """The only way to search code chunks (NFR-SEC-06).

    Exactly one of ``dense`` or ``sparse`` selects the mode; hybrid fusion arrives in P2.1
    inside this same function. ``repo_id`` and ``index_version`` have no defaults and are
    always applied as filters, so a search can never cross repositories or index versions.
    """
    if (dense is None) == (sparse is None):
        raise ValueError("pass exactly one of dense or sparse")
    query_filter = _scope(repo_id, index_version, exclude_tests)
    if dense is not None:
        response = client.query_points(
            collection,
            query=[float(x) for x in dense],
            using=DENSE,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
    else:
        assert sparse is not None
        if not sparse[0]:
            return []  # a query with no usable terms matches nothing
        response = client.query_points(
            collection,
            query=models.SparseVector(indices=list(sparse[0]), values=list(sparse[1])),
            using=SPARSE,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
    return [_hit(p.score, p.payload or {}) for p in response.points]


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
