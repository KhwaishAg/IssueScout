"""P1.1 'Done when': TC-03, TC-04 and TC-05 on the fixture repository, plus edge cases."""

import itertools
from pathlib import Path

import pytest

from issuescout.indexing import chunker
from issuescout.indexing.chunker import chunk_file, module_name
from issuescout.indexing.models import Chunk, content_hash
from issuescout.indexing.tokens import count_tokens
from issuescout.settings import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)


def _by_symbol(chunks: list[Chunk]) -> dict[str, Chunk]:
    return {c.symbol: c for c in chunks}


def _fixture_chunks(repo: Path, name: str) -> list[Chunk]:
    return chunk_file(f"pkg/{name}", (repo / "pkg" / name).read_bytes(), _settings())


# -- TC-03: nested classes and decorators ------------------------------------------------


def test_tc03_qualified_names_and_line_ranges(sample_repo: Path) -> None:
    chunks = _fixture_chunks(sample_repo, "shapes.py")
    got = {(c.symbol, c.kind, c.start_line, c.end_line) for c in chunks}
    assert got == {
        ("pkg.shapes", "module", 1, 40),
        ("pkg.shapes.area", "function", 8, 10),
        ("pkg.shapes.decorated", "function", 13, 15),  # starts at the decorator line
        ("pkg.shapes.Outer", "class", 18, 19),  # signature through docstring
        ("pkg.shapes.Outer.method_a", "method", 23, 24),
        ("pkg.shapes.Outer.prop", "method", 26, 28),
        ("pkg.shapes.Outer.Inner", "class", 30, 30),
        ("pkg.shapes.Outer.Inner.deep", "method", 31, 32),
        ("pkg.shapes.fetch", "function", 35, 36),
    }


def test_tc03_signature_parent_and_text(sample_repo: Path) -> None:
    by = _by_symbol(_fixture_chunks(sample_repo, "shapes.py"))
    assert by["pkg.shapes.fetch"].signature == "async def fetch(url, *, retries=3):"
    assert by["pkg.shapes.decorated"].text.startswith("@staticmethod_like\ndef decorated")
    assert by["pkg.shapes.decorated"].signature == "def decorated(x):"
    assert by["pkg.shapes.Outer.prop"].parent_symbol == "pkg.shapes.Outer"
    assert by["pkg.shapes.Outer.Inner.deep"].parent_symbol == "pkg.shapes.Outer.Inner"
    assert by["pkg.shapes.area"].parent_symbol == "pkg.shapes"
    assert by["pkg.shapes.Outer"].signature == "class Outer(Base):"


def test_class_header_lists_methods_and_docstring(sample_repo: Path) -> None:
    header = _by_symbol(_fixture_chunks(sample_repo, "shapes.py"))["pkg.shapes.Outer"].text
    assert "class Outer(Base):" in header
    assert '"""Outer class."""' in header
    assert header.endswith("# methods: method_a, prop")


def test_module_chunk_holds_only_leftover_code(sample_repo: Path) -> None:
    module = _by_symbol(_fixture_chunks(sample_repo, "shapes.py"))["pkg.shapes"].text
    assert "import math" in module
    assert 'if __name__ == "__main__":' in module
    assert "def area" not in module


def test_every_chunk_flagged_and_sized(sample_repo: Path) -> None:
    for c in _fixture_chunks(sample_repo, "shapes.py"):
        assert c.language == "python"
        assert c.is_test is False
        assert c.token_count == count_tokens(c.text) > 0


def test_extraction_is_deterministic(sample_repo: Path) -> None:
    assert _fixture_chunks(sample_repo, "shapes.py") == _fixture_chunks(sample_repo, "shapes.py")


# -- TC-04: syntax error -> line-window fallback -----------------------------------------


def test_tc04_syntax_error_falls_back_to_line_windows(sample_repo: Path) -> None:
    chunks = _fixture_chunks(sample_repo, "broken.py")
    assert [c.kind for c in chunks] == ["window"]
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 6)
    assert chunks[0].symbol == "pkg/broken.py"
    assert "def fine" in chunks[0].text  # the job continues; nothing is lost


def test_parse_timeout_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chunker, "PARSE_TIMEOUT_SECONDS", -1.0)
    chunks = chunk_file("a.py", b"def f():\n    return 1\n" * 50, _settings())
    assert chunks
    assert {c.kind for c in chunks} == {"window"}


# -- TC-05: oversized function -> four windows, each carrying the signature --------------


def test_tc05_function_of_1200_tokens_gives_four_windows() -> None:
    sig = "def big(a):"
    body = "\n".join(f"    v{i} = a" for i in range(398))  # 3 tokens per line
    source = f"{sig}\n{body}\n"
    assert count_tokens(source) == 1200
    chunks = chunk_file("big.py", source.encode(), _settings())
    funcs = [c for c in chunks if c.symbol == "big.big"]
    assert len(funcs) == 4
    assert [c.part for c in funcs] == [0, 1, 2, 3]
    assert all(c.parts == 4 for c in funcs)
    assert all(sig in c.text for c in funcs)
    # Overlapping, contiguous coverage of the whole function.
    assert funcs[0].start_line == 1
    assert funcs[-1].end_line == 399
    for earlier, later in itertools.pairwise(funcs):
        assert later.start_line <= earlier.end_line
    # Windows snap outward to whole lines: at most 400 tokens plus one partial line (3
    # tokens here) at each end, plus the prepended signature.
    assert all(c.token_count <= 400 + 2 * 3 + count_tokens(sig) for c in funcs)


def test_function_at_limit_is_not_split() -> None:
    body = "\n".join(f"    v{i} = a" for i in range(131))  # 6 + 393 = 399 tokens
    chunks = chunk_file("ok.py", f"def f(a):\n{body}\n".encode(), _settings())
    assert [c.part for c in chunks if c.symbol == "ok.f"] == [None]


# -- line windows, documents, and robustness ---------------------------------------------


def test_line_windows_60_with_10_overlap() -> None:
    source = "\n".join(f"line {i}" for i in range(1, 121)).encode()
    chunks = chunk_file("notes.cfg", source, _settings())
    assert [(c.start_line, c.end_line) for c in chunks] == [(1, 60), (51, 110), (101, 120)]
    assert all(c.kind == "window" for c in chunks)


def test_markdown_is_a_doc_chunk() -> None:
    chunks = chunk_file("docs/readme.md", b"# Title\n\nSome text.\n", _settings())
    assert [(c.kind, c.language) for c in chunks] == [("doc", "text")]


def test_crlf_source_has_same_line_numbers(sample_repo: Path) -> None:
    raw = (sample_repo / "pkg" / "shapes.py").read_bytes()
    crlf = raw.replace(b"\n", b"\r\n")
    unix = chunk_file("pkg/shapes.py", raw, _settings())
    win = chunk_file("pkg/shapes.py", crlf, _settings())
    assert [(c.symbol, c.start_line, c.end_line) for c in unix] == [
        (c.symbol, c.start_line, c.end_line) for c in win
    ]
    assert [c.content_hash for c in unix] == [c.content_hash for c in win]


def test_content_hash_ignores_trailing_whitespace() -> None:
    assert content_hash("a = 1  \nb = 2\n") == content_hash("a = 1\nb = 2\n")
    assert content_hash("a = 1") != content_hash("a = 2")


def test_empty_and_comment_only_files() -> None:
    assert chunk_file("empty.py", b"", _settings()) == []
    only_comment = chunk_file("c.py", b"# just a comment\n", _settings())
    assert [c.kind for c in only_comment] == ["module"]


def test_invalid_utf8_does_not_crash() -> None:
    chunks = chunk_file("latin.py", b"x = '\xe9'\n\ndef f():\n    return 1\n", _settings())
    assert {c.symbol for c in chunks} == {"latin", "latin.f"}


def test_test_files_are_flagged() -> None:
    chunks = chunk_file("tests/test_x.py", b"def test_a():\n    assert True\n", _settings())
    assert all(c.is_test for c in chunks)


@pytest.mark.parametrize(
    ("path", "expected"),
    [("pkg/mod.py", "pkg.mod"), ("pkg/__init__.py", "pkg"), ("__init__.py", ""), ("a.py", "a")],
)
def test_module_name(path: str, expected: str) -> None:
    assert module_name(path) == expected
