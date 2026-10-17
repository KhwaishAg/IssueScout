"""Token counting for chunk sizes (FR-IDX-08).

The SRS measures chunks in model tokens, but the embedding model is not chosen until
P2.2. Until then chunk sizes use this deterministic approximation: each word or
punctuation mark is one token. Code tokenises to more model tokens than words, so
real model counts run higher; the counter is injectable so P2.2 can swap in the
chosen model's tokenizer without touching the chunker.
"""

import re
from collections.abc import Callable

_TOKEN = re.compile(r"\w+|[^\w\s]")

TokenCounter = Callable[[str], int]


def count_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


def token_line_indexes(lines: list[str]) -> list[int]:
    """For each token in ``lines`` joined, the index of the line it sits on."""
    owners: list[int] = []
    for i, line in enumerate(lines):
        owners.extend([i] * count_tokens(line))
    return owners
