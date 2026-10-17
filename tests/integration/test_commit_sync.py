"""P1.6: advancing one index from commit to commit, against a synthetic git history."""

from pathlib import Path

from qdrant_client import QdrantClient

from issuescout.evaluation.commit_sync import CommitIndexer
from issuescout.evaluation.configs import AblationConfig
from issuescout.evaluation.gitstore import RepoMirror
from issuescout.evaluation.prepare import (
    PreparedInstance,
    prepare_split,
    walk_split,
)
from issuescout.evaluation.swebench import EvalInstance
from issuescout.indexing.pipeline import CLI_INDEX_VERSION, repo_id_for_name
from issuescout.settings import Settings
from issuescout.storage.qdrant import count_points, path_has_points


def indexer(
    mirror: RepoMirror,
    client: QdrantClient,
    collection: str,
    tmp_path: Path,
    name: str = "o/r",
) -> CommitIndexer:
    return CommitIndexer(
        mirror,
        repo_name=name,
        settings=Settings(_env_file=None, state_dir=tmp_path / "state"),
        client=client,
        encoder=None,
        cache=None,
        collection=collection,
        state_file=tmp_path / "state.json",
    )


def paths_in_index(
    client: QdrantClient, collection: str, name: str, paths: list[str]
) -> dict[str, bool]:
    rid = repo_id_for_name(name)
    return {
        p: path_has_points(
            client, repo_id=rid, index_version=CLI_INDEX_VERSION, path=p, collection=collection
        )
        for p in paths
    }


def test_first_advance_is_a_full_index_that_skips_lock_files(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (c1, _, _) = history
    stats = indexer(mirror, qdrant_client, collection, tmp_path).advance(c1)
    assert stats.mode == "full"
    assert stats.files_indexed == 2  # a.py, b.py; uv.lock is skipped
    assert paths_in_index(qdrant_client, collection, "o/r", ["a.py", "b.py", "uv.lock"]) == {
        "a.py": True,
        "b.py": True,
        "uv.lock": False,
    }


def test_advancing_only_touches_the_files_that_changed(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (c1, c2, _) = history
    idx = indexer(mirror, qdrant_client, collection, tmp_path)
    idx.advance(c1)
    stats = idx.advance(c2)
    assert stats.mode == "incremental"
    assert stats.files_considered == 3  # a.py modified, b.py deleted, c.py added
    assert stats.files_indexed == 2  # a.py and c.py re-chunked; nothing else read
    assert stats.files_dropped == 2  # a.py (old chunks) and b.py
    assert paths_in_index(qdrant_client, collection, "o/r", ["a.py", "b.py", "c.py"]) == {
        "a.py": True,
        "b.py": False,
        "c.py": True,
    }


def test_incremental_result_equals_a_full_index_of_the_same_commit(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    """The defining property: syncing forward must give exactly the full index."""
    mirror, commits = history
    incremental = indexer(mirror, qdrant_client, collection, tmp_path, name="o/inc")
    for sha in commits:
        incremental.advance(sha)
    full = indexer(mirror, qdrant_client, collection, tmp_path / "other", name="o/full")
    full.advance(commits[-1])

    def snapshot(name: str) -> list[tuple[str, str, int, int]]:
        from issuescout.indexing.sparse import sparse_query_vector
        from issuescout.storage.qdrant import search_code

        hits = search_code(
            qdrant_client,
            repo_id=repo_id_for_name(name),
            index_version=CLI_INDEX_VERSION,
            sparse=sparse_query_vector("def return alpha beta gamma renamed py"),
            limit=100,
            collection=collection,
        )
        return sorted((h.path, h.symbol, h.start_line, h.end_line) for h in hits)

    assert snapshot("o/inc") == snapshot("o/full")
    assert count_points(
        qdrant_client, repo_id=repo_id_for_name("o/inc"), collection=collection
    ) == count_points(qdrant_client, repo_id=repo_id_for_name("o/full"), collection=collection)


def test_rename_is_a_delete_plus_an_add(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (c1, c2, c3) = history
    idx = indexer(mirror, qdrant_client, collection, tmp_path)
    idx.advance(c1)
    idx.advance(c2)
    idx.advance(c3)
    assert paths_in_index(qdrant_client, collection, "o/r", ["c.py", "renamed.py"]) == {
        "c.py": False,
        "renamed.py": True,
    }


def test_same_commit_twice_is_a_noop(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (c1, _, _) = history
    idx = indexer(mirror, qdrant_client, collection, tmp_path)
    idx.advance(c1)
    assert idx.advance(c1).mode == "unchanged"


def test_wiped_collection_triggers_a_full_rebuild_not_a_partial_index(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    from issuescout.storage.qdrant import delete_repository

    mirror, (c1, c2, _) = history
    idx = indexer(mirror, qdrant_client, collection, tmp_path)
    idx.advance(c1)
    delete_repository(qdrant_client, repo_id=repo_id_for_name("o/r"), collection=collection)
    stats = idx.advance(c2)  # state file says c1, but the store is empty
    assert stats.mode == "full"
    assert paths_in_index(qdrant_client, collection, "o/r", ["a.py", "c.py", "b.py"]) == {
        "a.py": True,
        "c.py": True,
        "b.py": False,
    }


def test_going_backwards_in_history_also_works(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (c1, c2, _) = history
    idx = indexer(mirror, qdrant_client, collection, tmp_path)
    idx.advance(c2)
    idx.advance(c1)
    assert paths_in_index(qdrant_client, collection, "o/r", ["b.py", "c.py"]) == {
        "b.py": True,
        "c.py": False,
    }


# -- preparing and walking a split --------------------------------------------------------


def _instance(iid: str, commit_sha: str, gold: str, patch: str) -> EvalInstance:
    return EvalInstance(
        instance_id=iid,
        repo="o/r",
        base_commit=commit_sha,
        problem_statement="x",
        created_at="2024-01-01T00:00:00Z",
        gold_files=(gold,),
        split="dev",
        patch=patch,
    )


PATCH_ALPHA = (
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n"
    " def alpha():\n-    return 1\n+    return 9\n"
)


def test_prepare_orders_by_commit_time_and_derives_gold_functions(
    history: tuple[RepoMirror, list[str]],
) -> None:
    mirror, (c1, c2, c3) = history
    # Deliberately listed newest first; the order must come from the commits, not the list.
    instances = [
        _instance("late", c3, "renamed.py", ""),
        _instance("early", c1, "a.py", PATCH_ALPHA),
        _instance("middle", c2, "a.py", ""),
    ]
    prepared = prepare_split(
        "dev", Settings(_env_file=None), instances=instances, mirror_factory=lambda repo: mirror
    )
    assert [p.instance.instance_id for p in prepared] == ["early", "middle", "late"]
    assert [p.order for p in prepared] == [0, 1, 2]
    assert all(p.gold_files_present for p in prepared)
    assert prepared[0].instance.gold_functions == ("a.py::a.alpha",)


def test_prepare_flags_a_gold_file_missing_at_the_base_commit(
    history: tuple[RepoMirror, list[str]],
) -> None:
    mirror, (c1, _, _) = history
    prepared = prepare_split(
        "dev",
        Settings(_env_file=None),
        instances=[_instance("bad", c1, "does_not_exist.py", "")],
        mirror_factory=lambda repo: mirror,
    )
    assert prepared[0].gold_files_present is False


def test_walk_visits_each_instance_with_the_index_at_its_base_commit(
    qdrant_client: QdrantClient, history: tuple[RepoMirror, list[str]], tmp_path: Path
) -> None:
    mirror, (c1, c2, c3) = history
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    prepared = prepare_split(
        "dev",
        settings,
        instances=[
            _instance("i1", c1, "b.py", ""),
            _instance("i2", c2, "a.py", ""),
            _instance("i3", c3, "renamed.py", ""),
        ],
        mirror_factory=lambda repo: mirror,
    )
    cfg = AblationConfig("WALKTEST", "ast", "sparse", "test")
    seen: list[tuple[str, str, bool]] = []

    def visit(item: PreparedInstance, stats: object) -> None:
        # At i1's commit b.py exists; at i2's and i3's it does not.
        present = paths_in_index(qdrant_client, cfg.collection, "o/r", ["b.py"])["b.py"]
        seen.append((item.instance.instance_id, getattr(stats, "mode", "?"), present))

    try:
        walk_split(
            prepared,
            settings,
            config=cfg,
            client=qdrant_client,
            encoder=None,
            cache=None,
            visit=visit,
            mirror_factory=lambda repo: mirror,
        )
    finally:
        if qdrant_client.collection_exists(cfg.collection):
            qdrant_client.delete_collection(cfg.collection)
    assert seen == [
        ("i1", "full", True),
        ("i2", "incremental", False),
        ("i3", "incremental", False),
    ]
