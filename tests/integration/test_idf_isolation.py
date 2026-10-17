"""Regression tests for a flaw found by measurement while building P1.7.

With Qdrant's own IDF modifier, adding an unrelated repository to the collection moved
another repository's top sparse score from 23.8 to 34.4 and reordered its results, and
deleting then re-adding identical content also changed scores. IDF is now computed per
repository (indexing/termstats.py). These tests pin the properties that fix gives:

* a repository's scores do not depend on what else is in the collection;
* they do not depend on write history;
* the stored statistics always equal statistics rebuilt from the index.
"""

from pathlib import Path

from qdrant_client import QdrantClient

from issuescout.evaluation.commit_sync import CommitIndexer
from issuescout.evaluation.gitstore import RepoMirror
from issuescout.indexing.pipeline import (
    CLI_INDEX_VERSION,
    rebuild_termstats,
    repo_id_for_name,
    termstats_store,
)
from issuescout.indexing.termstats import TermStats
from issuescout.retrieval.baseline import retrieve_hits
from issuescout.settings import Settings
from issuescout.storage.qdrant import count_points
from tests.integration.git_helpers import commit, git

QUERY = "alpha return gamma beta renamed"


def settings_for(tmp_path: Path, collection: str) -> Settings:
    return Settings(_env_file=None, state_dir=tmp_path / "state", qdrant_collection=collection)


def make_indexer(
    mirror: RepoMirror, client: QdrantClient, settings: Settings, name: str
) -> CommitIndexer:
    return CommitIndexer(
        mirror,
        repo_name=name,
        settings=settings,
        client=client,
        encoder=None,
        cache=None,
        collection=settings.qdrant_collection,
        state_file=settings.state_dir / "sync.json",
    )


def scores(client: QdrantClient, settings: Settings, name: str) -> list[tuple[str, float]]:
    hits = retrieve_hits(
        QUERY,
        client=client,
        repo_id=repo_id_for_name(name),
        index_version=CLI_INDEX_VERSION,
        mode="sparse",
        settings=settings,
    )
    return [(f"{h.path}:{h.start_line}", h.score) for h in hits]


def noisy_mirror(tmp_path: Path) -> tuple[RepoMirror, str]:
    """A second repository whose files share vocabulary with the first (alpha, return...)."""
    origin = tmp_path / "noise_origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    for i in range(40):
        (origin / f"m{i}.py").write_text(
            f"def alpha_{i}():\n    return {i}\n\n\ndef beta_{i}(alpha):\n    return alpha\n"
        )
    sha = commit(origin, "noise", 1_700_000_000)
    return RepoMirror.ensure(origin.as_uri(), tmp_path / "noise_mirror.git"), sha


def test_another_repository_in_the_collection_does_not_change_scores(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (_, _, c3) = history
    settings = settings_for(tmp_path, collection)
    make_indexer(mirror, qdrant_client, settings, "o/r").advance(c3)
    before = scores(qdrant_client, settings, "o/r")
    assert before  # the query matches something

    noise, noise_sha = noisy_mirror(tmp_path)
    make_indexer(noise, qdrant_client, settings, "o/noise").advance(noise_sha)
    after = scores(qdrant_client, settings, "o/r")

    assert after == before  # exact: same files, same order, same floating-point scores


def test_write_history_does_not_change_scores(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (c1, c2, c3) = history
    settings = settings_for(tmp_path, collection)
    indexer = make_indexer(mirror, qdrant_client, settings, "o/r")
    indexer.advance(c1)
    first = scores(qdrant_client, settings, "o/r")

    indexer.advance(c2)  # modifies, deletes and adds files ...
    indexer.advance(c3)
    indexer.advance(c1)  # ... and back: identical content, very different write history
    assert scores(qdrant_client, settings, "o/r") == first


def test_stored_statistics_equal_statistics_rebuilt_from_the_index(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (c1, c2, c3) = history
    settings = settings_for(tmp_path, collection)
    indexer = make_indexer(mirror, qdrant_client, settings, "o/r")
    for sha in (c1, c2, c3, c1, c3, c2):
        indexer.advance(sha)

    repo_id = repo_id_for_name("o/r")
    stored = termstats_store(settings).load(collection, repo_id)
    rebuilt = rebuild_termstats(qdrant_client, repo_id, collection)
    assert stored == rebuilt
    assert stored.n_docs == count_points(qdrant_client, repo_id=repo_id, collection=collection)


def test_lost_statistics_are_rebuilt_and_scores_are_unchanged(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (_, _, c3) = history
    settings = settings_for(tmp_path, collection)
    make_indexer(mirror, qdrant_client, settings, "o/r").advance(c3)
    before = scores(qdrant_client, settings, "o/r")

    termstats_store(settings).delete(collection, repo_id_for_name("o/r"))  # simulate a crash
    assert scores(qdrant_client, settings, "o/r") == before


def test_out_of_date_statistics_are_detected_and_repaired(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (_, _, c3) = history
    settings = settings_for(tmp_path, collection)
    make_indexer(mirror, qdrant_client, settings, "o/r").advance(c3)
    before = scores(qdrant_client, settings, "o/r")

    wrong = TermStats(999, {1: 5})
    termstats_store(settings).save(collection, repo_id_for_name("o/r"), wrong)
    assert scores(qdrant_client, settings, "o/r") == before


def test_indexing_the_same_commit_twice_does_not_double_count(
    qdrant_client: QdrantClient,
    collection: str,
    history: tuple[RepoMirror, list[str]],
    tmp_path: Path,
) -> None:
    mirror, (c1, _, _) = history
    settings = settings_for(tmp_path, collection)
    make_indexer(mirror, qdrant_client, settings, "o/r").advance(c1)
    (settings.state_dir / "sync.json").unlink()  # forget progress: forces a full re-index
    make_indexer(mirror, qdrant_client, settings, "o/r").advance(c1)

    repo_id = repo_id_for_name("o/r")
    stored = termstats_store(settings).load(collection, repo_id)
    assert stored.n_docs == count_points(qdrant_client, repo_id=repo_id, collection=collection)
    assert stored == rebuild_termstats(qdrant_client, repo_id, collection)
