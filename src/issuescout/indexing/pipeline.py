"""Index one repository into Qdrant (SRS 4.3 steps 2-8, local-folder form).

select files -> chunk -> embed (cached) -> upsert -> delete points that no longer exist.
Re-running on an unchanged tree changes nothing; after edits it adds the new chunks and
removes the stale ones, so the index always equals the tree. The production path
(P3.5) builds a new index version and switches on success instead of rewriting in place.
"""

import hashlib
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from git import InvalidGitRepositoryError, NoSuchPathError, Repo

from issuescout.indexing.chunker import chunk_file
from issuescout.indexing.dense import DenseEncoder, EmbeddingCache, embed_texts
from issuescout.indexing.filters import select_files
from issuescout.indexing.models import Chunk
from issuescout.indexing.sparse import document_terms, embedding_text, sparse_document_vector
from issuescout.indexing.termstats import TermStats, TermStatsStore
from issuescout.settings import Settings
from issuescout.storage.qdrant import (
    QdrantClient,
    StoredPoint,
    count_points,
    delete_stale_points,
    ensure_collection,
    existing_point_ids,
    fetch_repo_points,
    point_id,
    upsert_chunks,
)

log = structlog.get_logger()

# Local indexing has one live version. Versioned rebuilds arrive with P3.5.
CLI_INDEX_VERSION = 1
CLI_INSTALLATION_ID = 0
GROUP_SIZE = 256  # chunks embedded and written together; bounds memory, saves progress


def repo_id_for_name(name: str) -> int:
    """Stable positive 56-bit integer for a repository name (fits Qdrant's int64)."""
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=7).digest()
    return int.from_bytes(digest, "big")


def head_commit(root: Path) -> str:
    try:
        return str(Repo(root, search_parent_directories=False).head.commit.hexsha)
    except (InvalidGitRepositoryError, NoSuchPathError, ValueError):
        return "local"


@dataclass
class IndexStats:
    name: str
    repo_id: int
    commit_sha: str
    files: int = 0
    chunks: int = 0
    removed_stale: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    embed_cache_hits: int = 0
    embed_cache_misses: int = 0

    @property
    def chunks_per_second(self) -> float:
        return self.chunks / self.seconds if self.seconds else 0.0


def _grouped(items: list[Chunk], size: int) -> Iterator[list[Chunk]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def termstats_store(settings: Settings) -> TermStatsStore:
    return TermStatsStore(settings.state_dir / "termstats")


def rebuild_termstats(client: QdrantClient, repo_id: int, collection: str) -> TermStats:
    """Recompute a repository's term statistics from the chunks actually in the index."""
    stats = TermStats()
    for point in fetch_repo_points(
        client, repo_id=repo_id, index_version=CLI_INDEX_VERSION, collection=collection
    ):
        stats.add(document_terms(point.text))
    return stats


def load_termstats(
    settings: Settings, client: QdrantClient, repo_id: int, collection: str
) -> TermStats:
    """Stored statistics, rebuilt from the index if they no longer match it.

    The saved file and the vector store are updated by separate writes, so a crash can
    leave them apart. The check is the number of chunks: if it differs, trust the index.
    """
    store = termstats_store(settings)
    stats = store.load(collection, repo_id)
    live = (
        count_points(
            client, repo_id=repo_id, index_version=CLI_INDEX_VERSION, collection=collection
        )
        if client.collection_exists(collection)
        else 0
    )
    if stats.n_docs != live:
        log.warning("termstats_rebuilt", repo_id=repo_id, stored=stats.n_docs, live=live)
        stats = rebuild_termstats(client, repo_id, collection)
        store.save(collection, repo_id, stats)
    return stats


def forget_points(stats: TermStats) -> Callable[[list[StoredPoint]], None]:
    """A callback for ``on_delete``: remove deleted points' terms from ``stats``."""

    def callback(points: list[StoredPoint]) -> None:
        for point in points:
            stats.remove(document_terms(point.text))

    return callback


def embed_and_upsert(
    chunks: list[Chunk],
    *,
    settings: Settings,
    client: QdrantClient,
    encoder: DenseEncoder | None,
    cache: EmbeddingCache | None,
    repo_id: int,
    commit_sha: str,
    collection: str,
    termstats: TermStats | None = None,
) -> set[str]:
    """Encode a group of chunks and write them. Returns the point IDs written.

    If ``termstats`` is given it is updated for every chunk that is new to the index, and
    only those: re-writing an existing point must not count its terms twice.

    Shared by the local CLI index and the benchmark's commit-to-commit sync, so both
    build identical points from identical chunks.
    """
    texts = [embedding_text(c) for c in chunks]
    dense: list[list[float]] | None = None
    if encoder is not None:
        if cache is None:
            raise ValueError("a dense encoder needs an embedding cache")
        dense = [v.tolist() for v in embed_texts(texts, encoder, cache, settings.dense_batch_size)]
    ids = [point_id(repo_id, CLI_INDEX_VERSION, c) for c in chunks]
    new_terms: dict[str, set[int]] = {}
    if termstats is not None:
        already = existing_point_ids(client, list(dict.fromkeys(ids)), collection)
        for point, text in zip(ids, texts, strict=True):
            if point not in already and point not in new_terms:
                new_terms[point] = document_terms(text)
    ensure_collection(client, len(dense[0]) if dense else None, collection)
    upsert_chunks(
        client,
        chunks,
        dense,
        [sparse_document_vector(t, settings) for t in texts],
        repo_id=repo_id,
        installation_id=CLI_INSTALLATION_ID,
        commit_sha=commit_sha,
        index_version=CLI_INDEX_VERSION,
        collection=collection,
    )
    if termstats is not None:
        for terms in new_terms.values():
            termstats.add(terms)
    return set(ids)


def index_repository(
    root: Path,
    *,
    name: str,
    settings: Settings,
    client: QdrantClient,
    encoder: DenseEncoder | None,
    cache: EmbeddingCache | None,
    ast: bool = True,
    collection: str | None = None,
) -> IndexStats:
    """Index ``root``. ``encoder=None`` builds a sparse-only index (no model is loaded)."""
    collection = collection or settings.qdrant_collection
    started = time.perf_counter()
    repo_id = repo_id_for_name(name)
    stats = IndexStats(name=name, repo_id=repo_id, commit_sha=head_commit(root))
    skipped: Counter[str] = Counter()

    chunks: list[Chunk] = []
    for rel, data in select_files(root, settings, skipped=skipped):
        stats.files += 1
        chunks.extend(chunk_file(rel, data, settings, ast=ast))
    stats.skipped = dict(skipped)

    hits_before = cache.hits if cache else 0
    misses_before = cache.misses if cache else 0

    termstats = load_termstats(settings, client, repo_id, collection)
    keep: set[str] = set()
    for group in _grouped(chunks, GROUP_SIZE):
        keep |= embed_and_upsert(
            group,
            settings=settings,
            client=client,
            encoder=encoder,
            cache=cache,
            repo_id=repo_id,
            commit_sha=stats.commit_sha,
            collection=collection,
            termstats=termstats,
        )
    stats.chunks = len(chunks)

    if chunks:
        stats.removed_stale = delete_stale_points(
            client,
            repo_id=repo_id,
            index_version=CLI_INDEX_VERSION,
            keep_ids=keep,
            collection=collection,
            on_delete=forget_points(termstats),
        )
    termstats_store(settings).save(collection, repo_id, termstats)
    if cache is not None:
        stats.embed_cache_hits = cache.hits - hits_before
        stats.embed_cache_misses = cache.misses - misses_before
    stats.seconds = time.perf_counter() - started
    log.info(
        "index_done",
        repo=name,
        files=stats.files,
        chunks=stats.chunks,
        removed_stale=stats.removed_stale,
        seconds=round(stats.seconds, 2),
    )
    return stats
