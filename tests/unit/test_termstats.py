"""Per-repository document frequencies and the IDF they give (indexing/termstats.py)."""

import math
from pathlib import Path

import pytest

from issuescout.indexing.models import Chunk
from issuescout.indexing.sparse import (
    document_terms,
    embedding_text,
    payload_embedding_text,
    sparse_document_vector,
    sparse_query_vector,
    term_index,
)
from issuescout.indexing.termstats import TermStats, TermStatsStore
from issuescout.settings import Settings


def test_add_counts_each_term_once_per_document() -> None:
    stats = TermStats()
    stats.add([1, 1, 1, 2])
    stats.add([2, 3])
    assert stats.n_docs == 2
    assert stats.df == {1: 1, 2: 2, 3: 1}


def test_remove_undoes_add_exactly() -> None:
    stats = TermStats()
    stats.add([1, 2])
    stats.add([2, 3])
    stats.remove([2, 3])
    assert stats == TermStats(1, {1: 1, 2: 1})
    stats.remove([1, 2])
    assert stats == TermStats()  # zero counts are dropped, not kept as 0


def test_removing_something_never_added_is_a_bookkeeping_error() -> None:
    stats = TermStats()
    with pytest.raises(ValueError, match="empty"):
        stats.remove([1])
    stats.add([1])
    with pytest.raises(ValueError, match="not in the statistics"):
        stats.remove([9])


def test_idf_matches_the_bm25_formula_worked_by_hand() -> None:
    """N=10 documents, a term in 1 of them: ln(1 + (10 - 1 + 0.5) / (1 + 0.5)) = ln(7.3333)."""
    stats = TermStats(10, {7: 1, 8: 9})
    assert stats.idf(7) == pytest.approx(math.log(1 + 9.5 / 1.5))
    assert stats.idf(7) == pytest.approx(1.99243, abs=1e-5)
    # A term in 9 of 10 documents carries little information, but stays positive.
    assert stats.idf(8) == pytest.approx(math.log(1 + 1.5 / 9.5))
    assert 0 < stats.idf(8) < stats.idf(7)


def test_a_term_unseen_in_the_repository_has_the_highest_idf() -> None:
    stats = TermStats(10, {7: 1})
    assert stats.idf(99) > stats.idf(7)


def test_json_roundtrip_and_store(tmp_path: Path) -> None:
    stats = TermStats(3, {10: 2, 11: 1})
    assert TermStats.from_json(stats.to_json()) == stats
    store = TermStatsStore(tmp_path)
    assert store.load("coll", 5) == TermStats()  # nothing saved yet
    store.save("coll", 5, stats)
    assert store.load("coll", 5) == stats
    assert store.load("coll", 6) == TermStats()  # repositories do not share a file
    assert store.load("other", 5) == TermStats()  # nor do collections
    store.delete("coll", 5)
    assert store.load("coll", 5) == TermStats()


def test_a_corrupt_file_reads_as_empty_not_a_crash(tmp_path: Path) -> None:
    store = TermStatsStore(tmp_path)
    store.save("c", 1, TermStats(1, {1: 1}))
    (tmp_path / "c" / "1.json").write_text("{not json", encoding="utf-8")
    assert store.load("c", 1) == TermStats()  # the caller rebuilds from the index


def test_query_weights_come_from_the_supplied_idf() -> None:
    stats = TermStats(100, {term_index("common"): 90, term_index("rare"): 1})
    indices, weights = sparse_query_vector("common rare", stats.idf)
    by_term = dict(zip(indices, weights, strict=True))
    assert by_term[term_index("rare")] > by_term[term_index("common")] > 0
    assert sparse_query_vector("common") == ([term_index("common")], [1.0])  # no idf given


def test_document_terms_are_the_vector_indices() -> None:
    settings = Settings(_env_file=None)
    text = "def getUserName(self): return name"
    assert document_terms(text) == set(sparse_document_vector(text, settings)[0])


def test_payload_text_equals_the_text_used_at_index_time() -> None:
    chunk = Chunk(
        path="pkg/mod.py",
        language="python",
        kind="function",
        symbol="pkg.mod.f",
        parent_symbol="pkg.mod",
        signature="def f():",
        start_line=1,
        end_line=2,
        text="def f():\n    return 1",
        token_count=6,
        is_test=False,
    )
    payload = {"path": chunk.path, "symbol": chunk.symbol, "text": chunk.text}
    assert payload_embedding_text(payload) == embedding_text(chunk)
