"""Build small, deterministic git histories for tests."""

import os
import subprocess
from pathlib import Path


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def commit(origin: Path, message: str, when: int) -> str:
    """Commit everything in ``origin`` at a fixed time, so ordering by date is predictable."""
    git(origin, "add", "-A")
    stamp = f"{when} +0000"
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message],
        cwd=origin,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
    )
    return git(origin, "rev-parse", "HEAD")


def make_history(tmp_path: Path) -> tuple[Path, list[str]]:
    """Three commits: add files; modify one, delete one, add one; rename one.

    Returns the origin repository path and the three commit hashes, oldest first.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "a.py").write_text("def alpha():\n    return 1\n")
    (origin / "b.py").write_text("def beta():\n    return 2\n")
    (origin / "uv.lock").write_text("lock\n")
    c1 = commit(origin, "one", 1_700_000_000)

    (origin / "a.py").write_text("def alpha():\n    return 100\n")
    (origin / "b.py").unlink()
    (origin / "c.py").write_text("def gamma():\n    return 3\n")
    c2 = commit(origin, "two", 1_700_000_100)

    (origin / "c.py").rename(origin / "renamed.py")
    c3 = commit(origin, "three", 1_700_000_200)
    return origin, [c1, c2, c3]
