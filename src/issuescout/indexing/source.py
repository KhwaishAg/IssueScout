"""Turn the CLI's ``<path or URL>`` into a local directory to index (FR-CLI-01).

Remote repositories are cloned shallowly into ``<state_dir>/mirrors``. Cloning only
fetches objects: no hooks are copied, none run, and nothing in the repository is
executed (NFR-SEC-11).
"""

import hashlib
import os
from pathlib import Path

from git import Repo

from issuescout.settings import Settings

REMOTE_PREFIXES = ("http://", "https://", "ssh://", "git@", "file://")


class SourceError(Exception):
    """The given source does not exist or cannot be cloned."""


def is_remote(source: str) -> bool:
    return source.startswith(REMOTE_PREFIXES)


def default_name(source: str) -> str:
    """``https://github.com/o/repo.git`` -> ``repo``; ``/a/b/proj`` -> ``proj``."""
    tail = source.rstrip("/").replace("\\", "/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return tail.removesuffix(".git") or "repo"


def resolve_source(source: str, settings: Settings) -> Path:
    """Return a local directory for ``source``, cloning it first if it is a URL."""
    if not is_remote(source):
        path = Path(source).expanduser().resolve()
        if not path.is_dir():
            raise SourceError(f"{source!r} is not a directory")
        return path

    suffix = hashlib.sha256(source.encode()).hexdigest()[:8]
    dest = settings.state_dir / "mirrors" / f"{default_name(source)}-{suffix}"
    # Never prompt for credentials; never let git run an external protocol helper.
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "https:ssh:file"}
    try:
        if (dest / ".git").is_dir():
            repo = Repo(dest)
            with repo.git.custom_environment(**env):
                repo.git.fetch("--depth", "1", "origin")
                repo.git.reset("--hard", "FETCH_HEAD")
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            Repo.clone_from(
                source,
                dest,
                depth=1,
                multi_options=["--single-branch"],
                env={**os.environ, **env},
            )
    except Exception as exc:
        raise SourceError(f"could not clone {source}: {type(exc).__name__}") from exc
    return dest
