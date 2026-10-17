"""Read repositories straight from git objects, without checking anything out.

The benchmark needs the files of one repository at many commits. A checkout per commit
is slow and writes untrusted files to disk; reading blobs from a bare mirror is fast and
executes nothing (NFR-SEC-11). Every call runs ``git`` with an argument list (no shell).
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

_GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ALLOW_PROTOCOL": "https:ssh:file",
}


class GitError(Exception):
    pass


def _git(cwd: Path, *args: str, check: bool = True) -> bytes:
    proc = subprocess.run(["git", *args], cwd=cwd, env=_GIT_ENV, capture_output=True, check=False)
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args[:3])} failed: {proc.stderr.decode(errors='replace')[:300]}"
        )
    return proc.stdout


@dataclass(frozen=True)
class TreeEntry:
    path: str
    sha: str
    size: int


class RepoMirror:
    """A bare clone of one repository, readable at any commit."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def ensure(cls, url: str, path: Path) -> "RepoMirror":
        """Clone ``url`` as a bare mirror at ``path`` if it is not there yet."""
        path = path.resolve()  # git runs in path.parent, so a relative path would nest
        if not (path / "HEAD").is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            _git(path.parent, "clone", "--bare", "--quiet", url, str(path))
        return cls(path)

    def has_commit(self, sha: str) -> bool:
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=self.path,
            env=_GIT_ENV,
            capture_output=True,
            check=False,
        )
        return proc.returncode == 0

    def ensure_commit(self, sha: str) -> None:
        """Make ``sha`` available, fetching it by name if the clone does not have it."""
        if self.has_commit(sha):
            return
        _git(self.path, "fetch", "--quiet", "origin", sha)
        if not self.has_commit(sha):
            raise GitError(f"commit {sha} is not available in {self.path.name}")

    def commit_times(self, shas: list[str]) -> dict[str, int]:
        """Committer timestamps, used to order commits oldest first."""
        out = _git(self.path, "log", "--no-walk=unsorted", "--format=%H %ct", *shas)
        times = {}
        for line in out.decode().splitlines():
            sha, _, ts = line.partition(" ")
            times[sha] = int(ts)
        return times

    def list_tree(self, commit: str) -> list[TreeEntry]:
        """Regular files at ``commit`` (symlinks and submodules excluded) with their sizes."""
        out = _git(self.path, "ls-tree", "-r", "-l", "-z", "--full-tree", commit)
        entries = []
        for record in out.split(b"\0"):
            if not record:
                continue
            meta, _, name = record.partition(b"\t")
            mode, kind, sha, size = meta.decode().split()
            if kind != "blob" or mode == "120000":
                continue
            entries.append(TreeEntry(name.decode("utf-8", errors="replace"), sha, int(size)))
        return entries

    def read_blobs(self, shas: list[str]) -> dict[str, bytes]:
        """Contents of many blobs in one ``git cat-file --batch`` round trip."""
        if not shas:
            return {}
        request = ("\n".join(shas) + "\n").encode()
        proc = subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=self.path,
            env=_GIT_ENV,
            input=request,
            capture_output=True,
            check=True,
        )
        data, pos, blobs = proc.stdout, 0, {}
        for sha in shas:
            end = data.index(b"\n", pos)
            header = data[pos:end].decode().split()
            if len(header) < 3 or header[1] == "missing":
                raise GitError(f"blob {sha} is missing")
            size = int(header[2])
            blobs[sha] = data[end + 1 : end + 1 + size]
            pos = end + 1 + size + 1  # skip the trailing newline
        return blobs

    def read_file(self, commit: str, path: str) -> bytes | None:
        """One file at one commit, or None if it does not exist there."""
        proc = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=self.path,
            env=_GIT_ENV,
            capture_output=True,
            check=False,
        )
        return proc.stdout if proc.returncode == 0 else None

    def changed_files(self, old: str, new: str) -> list[tuple[str, str]]:
        """``(status, path)`` between two commits; status is A, M or D.

        Rename detection is off, so a rename is a delete plus an add (FR-SYN-04).
        """
        out = _git(self.path, "diff", "--name-status", "--no-renames", "-z", old, new)
        parts = [p for p in out.decode("utf-8", errors="replace").split("\0") if p]
        return [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]
