"""File selection (FR-IDX-03, NFR-SEC-10): decide which files are worth indexing.

Every rule returns a reason string when a file is skipped, so skips are countable and
debuggable instead of silent.
"""

import fnmatch
import os
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath

from issuescout.settings import Settings

SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "third_party",
        "dist",
        "build",
        "target",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "site-packages",
    }
)
LOCK_NAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "pipfile.lock",
        "cargo.lock",
        "go.sum",
        "composer.lock",
        "gemfile.lock",
    }
)
SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*")
MINIFIED_SUFFIXES = (".min.js", ".min.css")
MINIFIED_AVG_LINE = 300  # a file whose average line is this long is not human-written code
BINARY_SNIFF_BYTES = 8192

LANGUAGES = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".rs": "rust",
    ".rb": "ruby",
    ".sh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".cfg": "config",
    ".ini": "config",
}
DOC_SUFFIXES = (".md", ".rst", ".txt")


def detect_language(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in DOC_SUFFIXES:
        return "text"
    return LANGUAGES.get(suffix, "text")


def is_doc(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in DOC_SUFFIXES


def is_test_path(path: str) -> bool:
    """FR-IDX-14: test files are flagged so retrieval can down-weight them."""
    p = PurePosixPath(path)
    name = p.name.lower()
    dirs = {part.lower() for part in p.parts[:-1]}
    return (
        bool(dirs & {"test", "tests", "testing"})
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts"))
        or name == "conftest.py"
    )


def skip_reason(
    path: str,
    head: bytes,
    size: int,
    settings: Settings,
    exclude_paths: Sequence[str] = (),
) -> str | None:
    """Return why ``path`` must not be indexed, or None if it should be.

    ``head`` is the first bytes of the file (at least ``BINARY_SNIFF_BYTES`` if available).
    """
    p = PurePosixPath(path)
    name = p.name.lower()
    if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in p.parts[:-1]):
        return "vendored_or_build_dir"
    if any(fnmatch.fnmatch(name, pat) for pat in SECRET_PATTERNS):
        return "secret_file"
    if name in LOCK_NAMES or name.endswith(".lock"):
        return "lock_file"
    if name.endswith(MINIFIED_SUFFIXES):
        return "minified"
    if any(fnmatch.fnmatch(path, pat) for pat in exclude_paths):
        return "excluded_by_config"
    if size > settings.max_file_bytes:
        return "too_large"
    if size == 0:
        return "empty"
    if b"\x00" in head[:BINARY_SNIFF_BYTES]:
        return "binary"
    lines = head.count(b"\n") + 1
    if size > 10_000 and len(head) / lines > MINIFIED_AVG_LINE:
        return "minified"
    return None


def select_files(
    root: Path,
    settings: Settings,
    exclude_paths: Sequence[str] = (),
) -> Iterator[tuple[str, bytes]]:
    """Yield ``(posix_path, content)`` for every indexable file under ``root``.

    Repository content is only read as bytes and never executed (NFR-SEC-11). Symlinks
    are not followed, so a hostile link cannot pull in files from outside the tree.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            if full.is_symlink():
                continue
            rel = full.relative_to(root).as_posix()
            size = full.stat().st_size
            with full.open("rb") as fh:
                head = fh.read(BINARY_SNIFF_BYTES)
            if skip_reason(rel, head, size, settings, exclude_paths) is None:
                yield rel, full.read_bytes()
