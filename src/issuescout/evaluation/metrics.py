"""Retrieval metrics (SRS 10.4): Hit@k, Recall@k, MRR and nDCG@k.

Pure functions over ranked lists of item names (file paths, or "path::symbol" for
functions) and the set of gold items for one instance. Relevance is binary: an item is
either in the gold set or not. A ranking is deduplicated first (keeping the first
occurrence), so listing the same file twice cannot inflate a score or push the real
hit down.

Every instance must have at least one gold item; an empty gold set is a data error, so
it raises instead of silently counting as 0 or 1.
"""

import math
from collections.abc import Collection, Sequence


def _top(ranked: Sequence[str], k: int) -> list[str]:
    if k < 1:
        raise ValueError("k must be at least 1")
    return list(dict.fromkeys(ranked))[:k]


def _check_gold(gold: Collection[str]) -> None:
    if not gold:
        raise ValueError("an instance must have at least one gold item")


def hit_at_k(ranked: Sequence[str], gold: Collection[str], k: int) -> float:
    """1.0 if any gold item is in the top ``k``, else 0.0."""
    _check_gold(gold)
    return 1.0 if any(item in gold for item in _top(ranked, k)) else 0.0


def recall_at_k(ranked: Sequence[str], gold: Collection[str], k: int) -> float:
    """Share of the gold items that appear in the top ``k``."""
    _check_gold(gold)
    found = {item for item in _top(ranked, k) if item in gold}
    return len(found) / len(set(gold))


def reciprocal_rank(ranked: Sequence[str], gold: Collection[str]) -> float:
    """1 / rank of the first gold item (rank starts at 1); 0.0 if none is ranked."""
    _check_gold(gold)
    for rank, item in enumerate(_top(ranked, len(ranked) or 1), start=1):
        if item in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[str], gold: Collection[str], k: int) -> float:
    """Discounted cumulative gain of the top ``k`` over the best possible ordering.

    A gold item at rank ``i`` contributes ``1 / log2(i + 1)``. The ideal ordering puts
    ``min(|gold|, k)`` gold items first, so a perfect ranking scores exactly 1.0.
    """
    _check_gold(gold)
    gold_set = set(gold)
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(_top(ranked, k), start=1)
        if item in gold_set
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(gold_set), k) + 1))
    return dcg / ideal


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("cannot average zero instances")
    return sum(values) / len(values)


def evaluate(
    rankings: Sequence[Sequence[str]], golds: Sequence[Collection[str]]
) -> dict[str, float]:
    """The headline file-level metrics averaged over instances (FR-EVL-03, SRS 10.4)."""
    if len(rankings) != len(golds):
        raise ValueError("need one gold set per ranking")
    pairs = list(zip(rankings, golds, strict=True))
    return {
        "hit@1": mean([hit_at_k(r, g, 1) for r, g in pairs]),
        "hit@5": mean([hit_at_k(r, g, 5) for r, g in pairs]),
        "recall@10": mean([recall_at_k(r, g, 10) for r, g in pairs]),
        "mrr": mean([reciprocal_rank(r, g) for r, g in pairs]),
        "ndcg@10": mean([ndcg_at_k(r, g, 10) for r, g in pairs]),
    }
