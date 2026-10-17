"""P1.4 'Done when': indexing a local repository and querying it prints ranked files."""

import hashlib
import json
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
import pytest
from qdrant_client import QdrantClient
from typer.testing import CliRunner

from issuescout import cli
from issuescout.cli import app
from issuescout.indexing.dense import EmbeddingCache, Vector
from issuescout.indexing.pipeline import (
    CLI_INDEX_VERSION,
    IndexStats,
    index_repository,
    repo_id_for_name,
)
from issuescout.retrieval.baseline import search_files
from issuescout.settings import Settings, get_settings
from issuescout.storage.qdrant import count_points, delete_repository

DIM = 16

FILES = {
    "net/retry.py": (
        "def compute_backoff(attempt, max_delay):\n"
        "    return min(2 ** attempt, max_delay)\n\n\n"
        "class RetryPolicy:\n"
        "    def delay(self, attempt):\n"
        "        return compute_backoff(attempt, 30)\n"
    ),
    "net/session.py": (
        "def send_request(url):\n    return open_socket(url)\n\n\n"
        "def open_socket(url):\n    return url\n"
    ),
    "config/defaults.py": "TIMEOUT = 30\nMAX_RETRIES = 3\n",
    "README.md": "# Demo\n\nA tiny project about http retries.\n",
}


class FakeEncoder:
    """Hash-based unit vectors: identical text gives identical vectors, no model needed."""

    model_id = "fake-hash@1"

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        out: list[Vector] = []
        for text in texts:
            raw = np.array(
                [(b - 127.5) / 127.5 for b in hashlib.sha256(text.encode()).digest()[:DIM]],
                dtype=np.float32,
            )
            out.append(raw / np.linalg.norm(raw))
        return out


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    for rel, body in FILES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    (root / "uv.lock").write_text("lock\n")  # must be skipped
    return root


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, state_dir=tmp_path / "state")


def _index(
    client: QdrantClient,
    collection: str,
    root: Path,
    settings: Settings,
    *,
    dense: bool = True,
    ast: bool = True,
) -> IndexStats:
    cache = EmbeddingCache(settings.state_dir / "embeddings") if dense else None
    return index_repository(
        root,
        name=root.name,
        settings=settings,
        client=client,
        encoder=FakeEncoder() if dense else None,
        cache=cache,
        ast=ast,
        collection=collection,
    )


def test_index_then_sparse_query_ranks_the_relevant_file_first(
    qdrant_client: QdrantClient, collection: str, repo: Path, settings: Settings
) -> None:
    stats = _index(qdrant_client, collection, repo, settings)
    assert stats.files == 4
    assert stats.skipped == {"lock_file": 1}
    assert stats.chunks > 0
    files = search_files(
        "the backoff delay ignores max_delay",
        client=qdrant_client,
        repo_id=repo_id_for_name("demo"),
        index_version=CLI_INDEX_VERSION,
        mode="sparse",
        settings=settings,
        collection=collection,
    )
    assert files[0].path == "net/retry.py"
    symbols = [s.symbol for s in files[0].symbols]
    assert "net.retry.compute_backoff" in symbols
    top = files[0].symbols[0]
    assert (top.start_line, top.end_line) == (1, 2)


def test_dense_query_finds_the_chunk_with_identical_text(
    qdrant_client: QdrantClient, collection: str, repo: Path, settings: Settings
) -> None:
    from issuescout.indexing.chunker import chunk_file
    from issuescout.indexing.sparse import embedding_text

    _index(qdrant_client, collection, repo, settings)
    target = next(
        c
        for c in chunk_file("net/session.py", FILES["net/session.py"].encode(), settings)
        if c.symbol.endswith("open_socket")
    )
    files = search_files(
        embedding_text(target),  # encoded by the fake exactly like the stored chunk
        client=qdrant_client,
        repo_id=repo_id_for_name("demo"),
        index_version=CLI_INDEX_VERSION,
        mode="dense",
        settings=settings,
        encoder=FakeEncoder(),
        collection=collection,
    )
    assert files[0].path == "net/session.py"
    assert files[0].symbols[0].symbol == "net.session.open_socket"


def test_reindexing_unchanged_tree_changes_nothing_and_reuses_embeddings(
    qdrant_client: QdrantClient, collection: str, repo: Path, settings: Settings
) -> None:
    first = _index(qdrant_client, collection, repo, settings)
    n = count_points(qdrant_client, repo_id=first.repo_id, collection=collection)
    second = _index(qdrant_client, collection, repo, settings)
    assert count_points(qdrant_client, repo_id=first.repo_id, collection=collection) == n
    assert second.removed_stale == 0
    assert second.embed_cache_misses == 0 and second.embed_cache_hits == first.chunks


def test_reindex_removes_chunks_of_deleted_and_edited_code(
    qdrant_client: QdrantClient, collection: str, repo: Path, settings: Settings
) -> None:
    first = _index(qdrant_client, collection, repo, settings)
    (repo / "net" / "session.py").unlink()
    (repo / "config" / "defaults.py").write_text("TIMEOUT = 99\n")
    second = _index(qdrant_client, collection, repo, settings)
    assert second.removed_stale >= 2
    files = search_files(
        "send_request open_socket",
        client=qdrant_client,
        repo_id=first.repo_id,
        index_version=CLI_INDEX_VERSION,
        mode="sparse",
        settings=settings,
        collection=collection,
    )
    assert all(f.path != "net/session.py" for f in files)
    assert (
        count_points(qdrant_client, repo_id=first.repo_id, collection=collection) == second.chunks
    )


def test_sparse_only_index_needs_no_encoder_or_dense_vectors(
    qdrant_client: QdrantClient, collection: str, repo: Path, settings: Settings
) -> None:
    delete_repository(qdrant_client, repo_id=repo_id_for_name("demo"), collection=collection)
    stats = _index(qdrant_client, collection, repo, settings, dense=False)
    assert stats.embed_cache_misses == 0
    files = search_files(
        "compute_backoff",
        client=qdrant_client,
        repo_id=stats.repo_id,
        index_version=CLI_INDEX_VERSION,
        mode="sparse",
        settings=settings,
        collection=collection,
    )
    assert files[0].path == "net/retry.py"


def test_line_window_chunking_for_the_a0_baseline(
    qdrant_client: QdrantClient, collection: str, repo: Path, settings: Settings
) -> None:
    ast = _index(qdrant_client, collection, repo, settings, dense=False, ast=True)
    lines = _index(qdrant_client, collection, repo, settings, dense=False, ast=False)
    assert lines.chunks == 4  # one window per file
    assert lines.chunks != ast.chunks
    assert count_points(qdrant_client, repo_id=lines.repo_id, collection=collection) == 4


# -- the command line --------------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, collection: str) -> Iterator[None]:
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("QDRANT_COLLECTION", collection)
    monkeypatch.setattr(cli, "make_encoder", lambda s: FakeEncoder())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_cli_index_then_query_prints_ranked_files_with_symbols(cli_env: None, repo: Path) -> None:
    runner = CliRunner()
    indexed = runner.invoke(app, ["index", str(repo), "--name", "demo", "--sparse-only"])
    assert indexed.exit_code == 0, indexed.output
    assert "indexed demo" in indexed.output
    assert "skipped: lock_file=1" in indexed.output

    result = runner.invoke(app, ["query", "backoff ignores max_delay", "--repo", "demo"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].startswith(" 1. net/retry.py")
    assert any("net.retry.compute_backoff  L1-2  [function]" in line for line in lines)


def test_cli_query_json(cli_env: None, repo: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["index", str(repo), "--name", "demo", "--sparse-only"])
    result = runner.invoke(app, ["query", "compute_backoff", "--repo", "demo", "--json"])
    data = json.loads(result.output)
    assert data[0]["rank"] == 1
    assert data[0]["path"] == "net/retry.py"
    assert data[0]["symbols"][0]["symbol"] == "net.retry.compute_backoff"


def test_cli_dense_mode_and_top_limit(cli_env: None, repo: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["index", str(repo), "--name", "demo"])
    result = runner.invoke(
        app, ["query", "anything", "--repo", "demo", "--mode", "dense", "--top", "2", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)) <= 2


def test_cli_query_for_unindexed_repository_says_so(cli_env: None) -> None:
    result = CliRunner().invoke(app, ["query", "x", "--repo", "never-indexed"])
    assert result.exit_code == 0
    assert "no results" in result.output


def test_cli_rejects_a_missing_directory(cli_env: None, tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["index", str(tmp_path / "nope")])
    assert result.exit_code == 2
    assert "not a directory" in result.output


def test_cli_rejects_a_bad_mode_and_chunking(cli_env: None, repo: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["query", "x", "--repo", "demo", "--mode", "fuzzy"]).exit_code == 2
    assert runner.invoke(app, ["index", str(repo), "--chunking", "words"]).exit_code == 2


def test_sparse_only_index_creates_a_collection_without_a_dense_vector(
    qdrant_client: QdrantClient, repo: Path, settings: Settings
) -> None:
    """Regression: sparse-only used to fail on a brand-new collection (size=None)."""
    name = "test_sparse_only_" + hashlib.sha1(str(repo).encode()).hexdigest()[:8]
    try:
        stats = _index(qdrant_client, name, repo, settings, dense=False)
        info = qdrant_client.get_collection(name).config.params
        assert not info.vectors  # no dense config
        assert info.sparse_vectors is not None and "sparse" in info.sparse_vectors
        files = search_files(
            "compute_backoff",
            client=qdrant_client,
            repo_id=stats.repo_id,
            index_version=CLI_INDEX_VERSION,
            mode="sparse",
            settings=settings,
            collection=name,
        )
        assert files[0].path == "net/retry.py"
    finally:
        qdrant_client.delete_collection(name)
