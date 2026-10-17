"""TC-21: a known ranking gives hand-computed Hit@k, Recall@k, MRR and nDCG values.

Worked example used throughout: ranking [a, b, c, d, e], gold {c, e, x}.

* Hit@1 = 0 (a is not gold); Hit@3 = 1 (c is at rank 3).
* Recall@5 = 2/3 (c and e found, x never ranked); Recall@2 = 0.
* RR = 1/3 (first gold item is at rank 3).
* nDCG@5: DCG = 1/log2(4) + 1/log2(6) = 0.5 + 0.386853 = 0.886853.
  Ideal = 1/log2(2) + 1/log2(3) + 1/log2(4) = 1 + 0.630930 + 0.5 = 2.130930.
  nDCG = 0.886853 / 2.130930 = 0.416181.
"""

import pytest

from issuescout.evaluation.metrics import (
    evaluate,
    hit_at_k,
    mean,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)

RANKED = ["a", "b", "c", "d", "e"]
GOLD = {"c", "e", "x"}


def test_tc21_hit_at_k() -> None:
    assert hit_at_k(RANKED, GOLD, 1) == 0.0
    assert hit_at_k(RANKED, GOLD, 2) == 0.0
    assert hit_at_k(RANKED, GOLD, 3) == 1.0
    assert hit_at_k(RANKED, GOLD, 10) == 1.0


def test_tc21_recall_at_k() -> None:
    assert recall_at_k(RANKED, GOLD, 2) == 0.0
    assert recall_at_k(RANKED, GOLD, 3) == pytest.approx(1 / 3)
    assert recall_at_k(RANKED, GOLD, 5) == pytest.approx(2 / 3)
    assert recall_at_k(RANKED, {"c", "e"}, 5) == 1.0


def test_tc21_reciprocal_rank() -> None:
    assert reciprocal_rank(RANKED, GOLD) == pytest.approx(1 / 3)
    assert reciprocal_rank(["c"], {"c"}) == 1.0
    assert reciprocal_rank(RANKED, {"zzz"}) == 0.0
    assert reciprocal_rank([], {"c"}) == 0.0


def test_tc21_ndcg_at_k() -> None:
    assert ndcg_at_k(RANKED, GOLD, 5) == pytest.approx(0.416181, abs=1e-6)
    # Only gold item c at rank 3, k=5: DCG = 1/log2(4) = 0.5; ideal = 1 (one gold item).
    assert ndcg_at_k(RANKED, {"c"}, 5) == pytest.approx(0.5)
    # gold {a, b}, ranking [b, x, a]: DCG = 1 + 0.5 = 1.5; ideal = 1 + 1/log2(3).
    assert ndcg_at_k(["b", "x", "a"], {"a", "b"}, 3) == pytest.approx(0.919721, abs=1e-6)


def test_perfect_ranking_scores_one_everywhere() -> None:
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 10) == pytest.approx(1.0)
    assert hit_at_k(["a", "b"], {"a", "b"}, 1) == 1.0
    assert recall_at_k(["a", "b"], {"a", "b"}, 10) == 1.0
    assert reciprocal_rank(["a", "b"], {"a", "b"}) == 1.0


def test_ndcg_ideal_is_capped_at_k() -> None:
    """With 3 gold items but k=2, a ranking of two gold items is already perfect."""
    assert ndcg_at_k(["a", "b", "z"], {"a", "b", "c"}, 2) == pytest.approx(1.0)


def test_no_gold_found_scores_zero() -> None:
    for fn in (hit_at_k, recall_at_k, ndcg_at_k):
        assert fn(["p", "q"], {"a"}, 10) == 0.0
    assert reciprocal_rank(["p", "q"], {"a"}) == 0.0


def test_duplicates_neither_inflate_nor_push_down() -> None:
    """Listing a file twice must not change the score (first occurrence counts)."""
    with_dupes = ["a", "a", "a", "c"]
    assert reciprocal_rank(with_dupes, {"c"}) == pytest.approx(1 / 2)
    assert hit_at_k(with_dupes, {"c"}, 2) == 1.0
    assert recall_at_k(["c", "c", "c"], {"c", "d"}, 5) == pytest.approx(0.5)
    assert ndcg_at_k(["c", "c"], {"c"}, 5) == pytest.approx(1.0)


def test_k_cuts_the_ranking() -> None:
    assert hit_at_k(RANKED, {"e"}, 4) == 0.0
    assert hit_at_k(RANKED, {"e"}, 5) == 1.0


def test_empty_gold_is_an_error_not_a_score() -> None:
    with pytest.raises(ValueError, match="gold"):
        hit_at_k(RANKED, set(), 5)
    with pytest.raises(ValueError, match="gold"):
        recall_at_k(RANKED, set(), 5)
    with pytest.raises(ValueError, match="gold"):
        reciprocal_rank(RANKED, set())
    with pytest.raises(ValueError, match="gold"):
        ndcg_at_k(RANKED, set(), 5)


def test_k_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        hit_at_k(RANKED, GOLD, 0)


def test_evaluate_averages_over_instances() -> None:
    rankings = [["a", "b", "c"], ["x", "y", "z"]]
    golds = [{"a"}, {"z"}]
    result = evaluate(rankings, golds)
    assert result["hit@1"] == pytest.approx(0.5)  # first instance only
    assert result["hit@5"] == pytest.approx(1.0)
    assert result["recall@10"] == pytest.approx(1.0)
    assert result["mrr"] == pytest.approx((1 + 1 / 3) / 2)
    assert result["ndcg@10"] == pytest.approx((1 + 0.5) / 2)
    assert set(result) == {"hit@1", "hit@5", "recall@10", "mrr", "ndcg@10"}


def test_evaluate_requires_matching_lengths_and_instances() -> None:
    with pytest.raises(ValueError, match="one gold set per ranking"):
        evaluate([["a"]], [{"a"}, {"b"}])
    with pytest.raises(ValueError, match="zero instances"):
        evaluate([], [])


def test_mean() -> None:
    assert mean([1.0, 2.0, 6.0]) == 3.0
