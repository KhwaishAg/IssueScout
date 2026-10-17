"""P1.4 pure logic: file roll-up, repo identity, and source resolution."""

import subprocess
from pathlib import Path

import pytest

from issuescout.indexing.pipeline import repo_id_for_name
from issuescout.indexing.source import SourceError, default_name, is_remote, resolve_source
from issuescout.retrieval.baseline import rank_files
from issuescout.settings import Settings
from issuescout.storage.qdrant import Hit


def hit(path: str, symbol: str, score: float, start: int = 1, end: int = 5) -> Hit:
    return Hit(score, path, symbol, "function", start, end, "text", False, 1, 1)


def test_file_scores_as_its_best_chunk_and_files_sort_descending() -> None:
    ranked = rank_files(
        [hit("a.py", "a.f", 0.4), hit("b.py", "b.g", 0.9), hit("a.py", "a.h", 0.7)], 3
    )
    assert [(f.path, f.score) for f in ranked] == [("b.py", 0.9), ("a.py", 0.7)]


def test_symbols_are_best_first_and_capped() -> None:
    hits = [hit("a.py", f"a.f{i}", i / 10) for i in range(1, 6)]
    (file,) = rank_files(hits, 3)
    assert [s.symbol for s in file.symbols] == ["a.f5", "a.f4", "a.f3"]


def test_windows_of_one_symbol_count_once_using_the_best_window() -> None:
    hits = [hit("a.py", "a.big", 0.3, 1, 20), hit("a.py", "a.big", 0.8, 15, 40)]
    (file,) = rank_files(hits, 3)
    assert len(file.symbols) == 1
    assert (file.symbols[0].start_line, file.symbols[0].end_line) == (15, 40)


def test_ties_break_on_path_so_ranking_is_deterministic() -> None:
    ranked = rank_files([hit("z.py", "z.f", 0.5), hit("a.py", "a.f", 0.5)], 3)
    assert [f.path for f in ranked] == ["a.py", "z.py"]
    assert rank_files([], 3) == []


def test_repo_id_is_stable_positive_and_distinct() -> None:
    assert repo_id_for_name("proj") == repo_id_for_name("proj")
    assert repo_id_for_name("proj") != repo_id_for_name("other")
    assert 0 < repo_id_for_name("proj") < 2**63


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ("https://github.com/o/repo.git", "repo"),
        ("https://github.com/o/repo/", "repo"),
        ("git@github.com:o/repo.git", "repo"),
        ("/a/b/proj", "proj"),
        (r"C:\a\proj", "proj"),
    ],
)
def test_default_name(source: str, name: str) -> None:
    assert default_name(source) == name


def test_is_remote() -> None:
    assert is_remote("https://github.com/o/r") and is_remote("git@github.com:o/r.git")
    assert not is_remote("/tmp/x") and not is_remote("relative/dir")


def test_resolve_local_directory(tmp_path: Path) -> None:
    assert resolve_source(str(tmp_path), Settings(_env_file=None)) == tmp_path.resolve()


def test_resolve_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SourceError, match="not a directory"):
        resolve_source(str(tmp_path / "nope"), Settings(_env_file=None))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_clone_and_refresh_a_remote_repository(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    (origin / "a.py").write_text("x = 1\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "one")
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")

    url = origin.as_uri()
    first = resolve_source(url, settings)
    assert (first / "a.py").read_text() == "x = 1\n"
    assert not (first / ".git" / "hooks" / "post-checkout").exists()

    (origin / "a.py").write_text("x = 2\n")
    _git(origin, "commit", "-q", "-am", "two")
    second = resolve_source(url, settings)
    assert second == first
    assert (second / "a.py").read_text() == "x = 2\n"  # refreshed in place, not re-cloned


def test_clone_failure_is_reported_not_raised_raw(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    with pytest.raises(SourceError, match="could not clone"):
        resolve_source((tmp_path / "missing-repo").as_uri(), settings)
