"""Per-repository document frequencies for the sparse (keyword) signal.

Why this exists: Qdrant can apply IDF itself (the ``IDF`` modifier), but it computes the
statistics over the whole collection, not over the repository a query is filtered to. We
measured the consequence: adding an unrelated repository changed another repository's top
score from 23.8 to 34.4 and reordered its results, and deleting then re-adding identical
content also moved scores. A ranking that depends on what else is installed, or on write
history, is neither reproducible (FR-RET-12) nor a fair benchmark.

So the modifier is off. Documents store the term-frequency weight only, and the IDF of
each query term is computed here from *this repository's* chunks and folded into the
query vector. The score is the same BM25 sum, but it depends only on the repository's own
live chunks.

``n_docs`` and ``df`` describe exactly the chunks currently in the index for one
repository. They are updated whenever chunks are added or removed, and can always be
rebuilt from the index itself, which is how drift is repaired.
"""

import json
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TermStats:
    n_docs: int = 0
    df: dict[int, int] = field(default_factory=dict)

    def add(self, terms: Iterable[int]) -> None:
        """Count one more document containing each distinct term in ``terms``."""
        self.n_docs += 1
        for term in set(terms):
            self.df[term] = self.df.get(term, 0) + 1

    def remove(self, terms: Iterable[int]) -> None:
        """Forget one document. Raises if that would make a count negative (a bookkeeping bug)."""
        if self.n_docs <= 0:
            raise ValueError("cannot remove a document from empty statistics")
        self.n_docs -= 1
        for term in set(terms):
            count = self.df.get(term, 0)
            if count <= 0:
                raise ValueError(f"term {term} is not in the statistics")
            if count == 1:
                del self.df[term]
            else:
                self.df[term] = count - 1

    def idf(self, term: int) -> float:
        """BM25 inverse document frequency, ``ln(1 + (N - n + 0.5) / (n + 0.5))``."""
        n = self.df.get(term, 0)
        return math.log(1.0 + (self.n_docs - n + 0.5) / (n + 0.5))

    def to_json(self) -> str:
        return json.dumps({"n_docs": self.n_docs, "df": self.df}, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "TermStats":
        raw = json.loads(text)
        return cls(int(raw["n_docs"]), {int(k): int(v) for k, v in raw["df"].items()})


class TermStatsStore:
    """One JSON file per (collection, repository). Production moves this to MongoDB."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, collection: str, repo_id: int) -> Path:
        return self.root / collection / f"{repo_id}.json"

    def load(self, collection: str, repo_id: int) -> TermStats:
        try:
            return TermStats.from_json(self._path(collection, repo_id).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            return TermStats()

    def save(self, collection: str, repo_id: int, stats: TermStats) -> None:
        path = self._path(collection, repo_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(stats.to_json(), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written file

    def delete(self, collection: str, repo_id: int) -> None:
        self._path(collection, repo_id).unlink(missing_ok=True)
