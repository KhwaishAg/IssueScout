"""P1.2: the code-aware sparse tokenizer and vectors (FR-IDX-12)."""

import pytest

from issuescout.indexing.models import Chunk
from issuescout.indexing.sparse import (
    embedding_text,
    sparse_document_vector,
    sparse_query_vector,
    sparse_tokens,
    term_index,
)
from issuescout.settings import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # camelCase: unsplit identifier kept, plus the parts
        ("getUserName", ["getusername", "get", "user", "name"]),
        # acronym boundary
        ("HTTPServer", ["httpserver", "http", "server"]),
        # snake_case
        ("get_user_name", ["get_user_name", "get", "user", "name"]),
        # dotted chain: whole chain, each segment, and the split parts of each segment
        ("os.path.join", ["os.path.join", "os", "path", "join"]),
        ("http.client.Session", ["http.client.session", "http", "client", "session"]),
        # hyphens
        ("max-retries", ["max-retries", "max", "retries"]),
        # digits split off camelCase parts
        ("parseXML2", ["parsexml2", "parse", "xml"]),
        # a plain word is just itself
        ("retry", ["retry"]),
    ],
)
def test_identifier_splitting_keeps_unsplit_form(text: str, expected: list[str]) -> None:
    assert sparse_tokens(text) == expected


def test_single_characters_are_dropped_but_longer_tokens_survive() -> None:
    assert sparse_tokens("a = b + cc") == ["cc"]


def test_punctuation_and_operators_are_ignored() -> None:
    assert sparse_tokens("x = foo(bar, baz)[0]") == ["foo", "bar", "baz"]


def test_issue_words_match_identifier_parts() -> None:
    """The point of the tokenizer: 'user name' in an issue finds getUserName in code."""
    doc, _ = sparse_document_vector("def getUserName(self): ...", _settings())
    query, _ = sparse_query_vector("the user name is wrong")
    assert {term_index("user"), term_index("name")} <= set(doc) & set(query)


def test_exact_identifier_in_issue_matches_unsplit_form() -> None:
    doc, _ = sparse_document_vector("def getUserName(self): ...", _settings())
    query, _ = sparse_query_vector("getUserName returns None")
    assert term_index("getusername") in set(doc) & set(query)


def test_term_index_is_stable_and_distinguishes_terms() -> None:
    assert term_index("retry") == term_index("retry")
    assert 0 <= term_index("retry") < 2**32
    assert term_index("retry") != term_index("retries")


def test_document_vector_is_sorted_unique_and_positive() -> None:
    indices, values = sparse_document_vector("foo foo bar getFoo", _settings())
    assert indices == sorted(set(indices))
    assert len(indices) == len(values)
    assert all(v > 0 for v in values)


def test_term_frequency_saturates() -> None:
    s = _settings()
    one = dict(zip(*sparse_document_vector("retry", s), strict=True))
    five = dict(zip(*sparse_document_vector("retry " * 5, s), strict=True))
    i = term_index("retry")
    assert five[i] > one[i]  # more occurrences weigh more ...
    assert five[i] < 5 * one[i]  # ... but sublinearly (BM25 saturation)


def test_longer_documents_weigh_each_term_less() -> None:
    s = _settings()
    short = dict(zip(*sparse_document_vector("retry", s), strict=True))
    padded = dict(zip(*sparse_document_vector("retry " + "filler " * 300, s), strict=True))
    i = term_index("retry")
    assert padded[i] < short[i]


def test_query_vector_ignores_repeats() -> None:
    once = sparse_query_vector("retry")
    many = sparse_query_vector("retry retry retry")
    assert once == many
    assert once[1] == [1.0]


def test_empty_text_gives_empty_vectors() -> None:
    assert sparse_document_vector("", _settings()) == ([], [])
    assert sparse_query_vector("") == ([], [])


def test_embedding_text_prefixes_path_and_symbol() -> None:
    chunk = Chunk(
        path="pkg/mod.py",
        language="python",
        kind="function",
        symbol="pkg.mod.retry",
        parent_symbol="pkg.mod",
        signature="def retry():",
        start_line=1,
        end_line=2,
        text="def retry():\n    pass",
        token_count=5,
        is_test=False,
    )
    assert embedding_text(chunk) == "pkg/mod.py\npkg.mod.retry\ndef retry():\n    pass"
