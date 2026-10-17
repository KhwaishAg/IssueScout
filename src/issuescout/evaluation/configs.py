"""Named retrieval configurations of the ablation (SRS 10.5).

Each row of the ablation table adds one technique to the one above it. A configuration
fixes how the index is built (chunking, which vectors) and how it is searched, and gets
its own Qdrant collection so rows never contaminate each other. Rows A3-A5 (fusion,
reranking, history) are added by Phase 2 as those stages exist.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AblationConfig:
    name: str
    chunking: Literal["lines", "ast"]
    mode: Literal["sparse", "dense", "hybrid"]
    description: str
    index: str | None = None  # name of the configuration whose index this one reuses

    @property
    def collection(self) -> str:
        """Configurations that search the same chunks and vectors share one collection."""
        return f"swe_{(self.index or self.name).lower()}"

    @property
    def needs_dense(self) -> bool:
        return self.mode in ("dense", "hybrid")


CONFIGS: dict[str, AblationConfig] = {
    "A0": AblationConfig("A0", "lines", "sparse", "line windows, keyword search"),
    "A1": AblationConfig("A1", "ast", "sparse", "function-level chunks, keyword search"),
    "A2": AblationConfig("A2", "ast", "dense", "function-level chunks, embedding search"),
    "A3": AblationConfig(
        "A3",
        "ast",
        "hybrid",
        "function-level chunks, keyword and embedding search fused (RRF)",
        index="A2",  # same chunks and both vectors as A2: no second index
    ),
}
