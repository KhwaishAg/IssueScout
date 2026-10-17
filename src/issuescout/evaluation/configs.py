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
    mode: Literal["sparse", "dense"]
    description: str

    @property
    def collection(self) -> str:
        return f"swe_{self.name.lower()}"

    @property
    def needs_dense(self) -> bool:
        return self.mode == "dense"


CONFIGS: dict[str, AblationConfig] = {
    "A0": AblationConfig("A0", "lines", "sparse", "line windows, keyword search"),
    "A1": AblationConfig("A1", "ast", "sparse", "function-level chunks, keyword search"),
    "A2": AblationConfig("A2", "ast", "dense", "function-level chunks, embedding search"),
}
