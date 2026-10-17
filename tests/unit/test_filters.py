"""P1.1 file filters (FR-IDX-03, FR-IDX-14, NFR-SEC-10)."""

from pathlib import Path

import pytest

from issuescout.indexing.filters import (
    detect_language,
    is_test_path,
    select_files,
    skip_reason,
)
from issuescout.settings import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)


def _reason(path: str, head: bytes = b"x = 1\n", size: int | None = None) -> str | None:
    return skip_reason(path, head, len(head) if size is None else size, _settings())


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("node_modules/lib/index.js", "vendored_or_build_dir"),
        ("src/vendor/x.py", "vendored_or_build_dir"),
        ("build/out.py", "vendored_or_build_dir"),
        ("pkg.egg-info/PKG-INFO", "vendored_or_build_dir"),
        ("uv.lock", "lock_file"),
        ("web/package-lock.json", "lock_file"),
        ("Cargo.lock", "lock_file"),
        ("static/app.min.js", "minified"),
        (".env", "secret_file"),
        (".env.production", "secret_file"),
        ("deploy/key.pem", "secret_file"),
        ("home/id_rsa", "secret_file"),
    ],
)
def test_skipped_by_name(path: str, reason: str) -> None:
    assert _reason(path) == reason


def test_regular_source_is_kept() -> None:
    assert _reason("src/app.py") is None
    assert _reason(".env.example") == "secret_file"  # skipped too: safer than judging contents


def test_size_empty_and_binary() -> None:
    assert _reason("big.py", size=500_001) == "too_large"
    assert _reason("ok.py", size=500_000) is None
    assert _reason("e.py", head=b"", size=0) == "empty"
    assert _reason("img.bin", head=b"\x89PNG\x00\x00") == "binary"


def test_minified_by_line_length() -> None:
    head = b"a=1;" * 4000  # one 16 KB line
    assert _reason("bundle.js", head=head, size=len(head)) == "minified"


def test_exclude_paths_from_config() -> None:
    got = skip_reason("docs/guide.md", b"x\n", 2, _settings(), exclude_paths=["docs/*"])
    assert got == "excluded_by_config"
    assert skip_reason("src/a.py", b"x\n", 2, _settings(), exclude_paths=["docs/*"]) is None


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/unit/test_a.py", True),
        ("pkg/test_a.py", True),
        ("pkg/a_test.py", True),
        ("pkg/conftest.py", True),
        ("web/app.spec.ts", True),
        ("src/app.py", False),
        ("src/contest.py", False),
    ],
)
def test_is_test_path(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


def test_detect_language() -> None:
    assert detect_language("a/b.py") == "python"
    assert detect_language("a/B.PY") == "python"
    assert detect_language("README.md") == "text"
    assert detect_language("Makefile") == "text"


def test_select_files_walks_and_filters(tmp_path: Path) -> None:
    files = {
        "src/app.py": b"x = 1\n",
        "src/util.py": b"y = 2\n",
        "node_modules/dep/index.js": b"x\n",
        "uv.lock": b"lock\n",
        ".env": b"SECRET=1\n",
        "logo.png": b"\x89PNG\x00\x00data",
        "empty.py": b"",
        "docs/guide.md": b"# hi\n",
    }
    for rel, data in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    got = [rel for rel, _ in select_files(tmp_path, _settings())]
    assert got == ["docs/guide.md", "src/app.py", "src/util.py"]  # sorted, deterministic
    got = [rel for rel, _ in select_files(tmp_path, _settings(), exclude_paths=["docs/*"])]
    assert got == ["src/app.py", "src/util.py"]


def test_select_files_ignores_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.py").write_text("x = 1\n")
    try:
        (repo / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks need extra privileges on this OS")
    assert [rel for rel, _ in select_files(repo, _settings())] == ["real.py"]
