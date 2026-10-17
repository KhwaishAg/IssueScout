"""Index one repository into Qdrant (SRS 4.3 steps 2-8, local-folder form).

select files -> chunk -> embed (cached) -> upsert -> delete points that no longer exist.
Re-running on an unchanged tree changes nothing; after edits it adds the new chunks and
removes the stale ones, so the index always equals the tree. The production path
(P3.5) builds a new index version and switches on success instead of rewriting in place.
"""

import hashlib
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from git import InvalidGitRepositoryError, NoSuchPathError, Repo

from issuescout.indexing.chunker import chunk_file
from issuescout.indexing.dense import DenseEncoder, EmbeddingCache, embed_texts
from issuescout.indexing.filters import select_files
from issuescout.indexing.models import Chunk
from issuescout.indexing.sparse import embedding_text, sparse_document_vector
from issuescout.settings import Settings
from issuescout.storage.qdrant import (
    QdrantClient,
    delete_stale_points,
    ensure_collection,
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
) -> set[str]:
    """Encode a group of chunks and write them. Returns the point IDs written.

    Shared by the local CLI index and the benchmark's commit-to-commit sync, so both
    build identical points from identical chunks.
    """
    texts = [embedding_text(c) for c in chunks]
    dense: list[list[float]] | None = None
    if encoder is not None:
        if cache is None:
            raise ValueError("a dense encoder needs an embedding cache")
        dense = [v.tolist() for v in embed_texts(texts, encoder, cache, settings.dense_batch_size)]
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
    return {point_id(repo_id, CLI_INDEX_VERSION, c) for c in chunks}


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
        )
    stats.chunks = len(chunks)

    if chunks:
        stats.removed_stale = delete_stale_points(
            client,
            repo_id=repo_id,
            index_version=CLI_INDEX_VERSION,
            keep_ids=keep,
            collection=collection,
        )
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
