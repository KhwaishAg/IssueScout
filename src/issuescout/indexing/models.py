"""The unit of indexing: one chunk of a file (SRS 6.1 payload, minus index metadata)."""

import hashlib
from dataclasses import dataclass
from typing import Literal

ChunkKind = Literal["function", "method", "class", "module", "window", "doc"]


def content_hash(text: str) -> str:
    """SHA-256 of the text with line endings and trailing spaces normalised."""
    normalised = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Chunk:
    path: str
    language: str
    kind: ChunkKind
    symbol: str  # qualified name, e.g. "pkg.mod.Session.send"; module/file name for windows
    parent_symbol: str | None
    signature: str  # declaration line(s); empty for windows and module chunks
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    text: str
    token_count: int
    is_test: bool
    # Set when an oversized chunk was split (FR-IDX-08): 0-based part index and total parts.
    part: int | None = None
    parts: int | None = None

    @property
    def content_hash(self) -> str:
        return content_hash(self.text)
