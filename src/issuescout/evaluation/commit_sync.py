"""Advance one repository's index from commit to commit (FR-EVL-02; SRS 4.4).

The benchmark asks about one repository at many base commits. Re-indexing from scratch
each time would re-chunk and re-embed almost every file. Instead one index per repository
is moved forward: git lists the files that changed between the previous and the next
commit, only those are re-chunked, the points of modified and deleted files are removed,
and the new chunks are written. Embeddings are cached by content, so even a re-chunked
file with unchanged functions costs no encoder time.

The state needed to resume (which commit the index is at) is a small JSON file, checked
against the vector store so a wiped collection triggers a full rebuild instead of a
silent partial index.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

from issuescout.evaluation.gitstore import RepoMirror, TreeEntry
from issuescout.indexing.chunker import chunk_file
from issuescout.indexing.dense import DenseEncoder, EmbeddingCache
from issuescout.indexing.filters import BINARY_SNIFF_BYTES, skip_reason
from issuescout.indexing.models import Chunk
from issuescout.indexing.pipeline import (
    CLI_INDEX_VERSION,
    GROUP_SIZE,
    embed_and_upsert,
    repo_id_for_name,
)
from issuescout.settings import Settings
from issuescout.storage.qdrant import (
    QdrantClient,
    count_points,
    delete_paths,
    delete_stale_points,
)

log = structlog.get_logger()

BLOB_GROUP = 400  # files read from git per round trip


@dataclass
class SyncStats:
    commit: str
    mode: Literal["full", "incremental", "unchanged"]
    files_considered: int = 0
    files_indexed: int = 0
    chunks: int = 0
    files_dropped: int = 0  # modified or deleted files whose old chunks were removed
    stale_points_removed: int = 0  # full rebuild only: leftovers not in the new tree
    seconds: float = 0.0


class CommitIndexer:
    def __init__(
        self,
        mirror: RepoMirror,
        *,
        repo_name: str,
        settings: Settings,
        client: QdrantClient,
        encoder: DenseEncoder | None,
        cache: EmbeddingCache | None,
        collection: str,
        state_file: Path,
        ast: bool = True,
    ) -> None:
        self.mirror = mirror
        self.repo_name = repo_name
        self.repo_id = repo_id_for_name(repo_name)
        self.settings = settings
        self.client = client
        self.encoder = encoder
        self.cache = cache
        self.collection = collection
        self.state_file = state_file
        self.ast = ast

    # -- state ---------------------------------------------------------------------

    def indexed_commit(self) -> str | None:
        """The commit the index is at, or None if unknown or the vector store was wiped."""
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        commit = state.get(self.repo_name)
        if not commit or not self.client.collection_exists(self.collection):
            return None
        if count_points(self.client, repo_id=self.repo_id, collection=self.collection) == 0:
            return None
        return str(commit)

    def _save(self, commit: str) -> None:
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state[self.repo_name] = commit
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # -- work ----------------------------------------------------------------------

    def advance(self, commit: str) -> SyncStats:
        """Bring the index to ``commit``. Safe to call repeatedly with the same commit."""
        started = time.perf_counter()
        self.mirror.ensure_commit(commit)
        previous = self.indexed_commit()
        entries = {e.path: e for e in self.mirror.list_tree(commit)}

        if previous == commit:
            return SyncStats(commit, "unchanged", seconds=time.perf_counter() - started)

        if previous is None:
            stats = SyncStats(commit, "full", files_considered=len(entries))
            keep = self._index_files(list(entries.values()), commit, stats)
            stats.stale_points_removed = delete_stale_points(
                self.client,
                repo_id=self.repo_id,
                index_version=CLI_INDEX_VERSION,
                keep_ids=keep,
                collection=self.collection,
            )
        else:
            changes = self.mirror.changed_files(previous, commit)
            stats = SyncStats(commit, "incremental", files_considered=len(changes))
            gone = [path for status, path in changes if status in ("M", "D")]
            if gone:
                delete_paths(
                    self.client,
                    repo_id=self.repo_id,
                    index_version=CLI_INDEX_VERSION,
                    paths=gone,
                    collection=self.collection,
                )
            stats.files_dropped = len(gone)
            to_index = [
                entries[path]
                for status, path in changes
                if status in ("A", "M") and path in entries
            ]
            self._index_files(to_index, commit, stats)

        self._save(commit)
        stats.seconds = time.perf_counter() - started
        log.info(
            "commit_synced",
            repo=self.repo_name,
            commit=commit[:10],
            mode=stats.mode,
            files_indexed=stats.files_indexed,
            chunks=stats.chunks,
            seconds=round(stats.seconds, 2),
        )
        return stats

    def _index_files(self, entries: list[TreeEntry], commit: str, stats: SyncStats) -> set[str]:
        """Chunk, encode and write the given files. Returns the point IDs written."""
        keep: set[str] = set()
        candidates = [e for e in entries if skip_reason(e.path, b"", e.size, self.settings) is None]
        for start in range(0, len(candidates), BLOB_GROUP):
            batch = candidates[start : start + BLOB_GROUP]
            blobs = self.mirror.read_blobs([e.sha for e in batch])
            chunks: list[Chunk] = []
            for entry in batch:
                data = blobs[entry.sha]
                head = data[:BINARY_SNIFF_BYTES]
                if skip_reason(entry.path, head, entry.size, self.settings) is not None:
                    continue
                stats.files_indexed += 1
                chunks.extend(chunk_file(entry.path, data, self.settings, ast=self.ast))
            for offset in range(0, len(chunks), GROUP_SIZE):
                keep |= embed_and_upsert(
                    chunks[offset : offset + GROUP_SIZE],
                    settings=self.settings,
                    client=self.client,
                    encoder=self.encoder,
                    cache=self.cache,
                    repo_id=self.repo_id,
                    commit_sha=commit,
                    collection=self.collection,
                )
            stats.chunks += len(chunks)
        return keep
