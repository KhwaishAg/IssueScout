"""Code-aware sparse encoding (FR-IDX-12; SRS 4.3 step 7, 5.2).

Natural-language tokenizers treat ``getUserName`` as one unknown word, so an issue that
says "user name" never matches it. This tokenizer splits identifiers on camelCase,
snake_case, hyphens and dots, and also keeps the unsplit identifier, so both
"get user name" and the exact ``getUserName`` match.

Terms become integer indices by hashing (Qdrant sparse vectors are index/value pairs), and
weights are the term-frequency half of BM25. Qdrant's IDF modifier supplies the other half.
"""

import re
import zlib
from collections import Counter
from collections.abc import Callable, Mapping

from issuescout.indexing.models import Chunk
from issuescout.settings import Settings

# A "chain" is identifier characters joined by dots or hyphens: os.path.join, max-retries.
_CHAIN = re.compile(r"[A-Za-z0-9_]+(?:[.\-][A-Za-z0-9_]+)*")
# camelCase boundaries: HTTPServer -> HTTP, Server; parseXML2 -> parse, XML, 2
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
_SEPARATORS = re.compile(r"[.\-]")
MIN_TOKEN_LENGTH = 2


def sparse_tokens(text: str) -> list[str]:
    """Lowercased terms: each unsplit identifier plus its split parts. Order is stable."""
    tokens: list[str] = []
    for chain in _CHAIN.findall(text):
        segments = _SEPARATORS.split(chain)
        if len(segments) > 1:
            tokens.append(chain)  # the unsplit dotted or hyphenated name
        for segment in segments:
            tokens.append(segment)  # the unsplit identifier
            snake = [p for p in segment.split("_") if p]
            parts = [m for p in snake for m in _CAMEL.findall(p)]
            if len(parts) > 1:
                tokens.extend(parts)
    return [t.lower() for t in tokens if len(t) >= MIN_TOKEN_LENGTH]


def term_index(term: str) -> int:
    """Stable 32-bit index for a term (CRC32 is deterministic across runs and platforms)."""
    return zlib.crc32(term.encode("utf-8"))


SparseVector = tuple[list[int], list[float]]


def sparse_document_vector(text: str, settings: Settings) -> SparseVector:
    """BM25 term-frequency weights for a document: tf*(k1+1) / (tf + k1*(1-b+b*dl/avgdl))."""
    counts = Counter(sparse_tokens(text))
    length = sum(counts.values())
    norm = settings.sparse_k1 * (
        1 - settings.sparse_b + settings.sparse_b * length / settings.sparse_avgdl
    )
    merged: dict[int, float] = {}
    for term, tf in counts.items():
        idx = term_index(term)
        # Two terms can share a CRC32; summing keeps the vector valid (indices unique).
        merged[idx] = merged.get(idx, 0.0) + tf * (settings.sparse_k1 + 1) / (tf + norm)
    ordered = sorted(merged)
    return ordered, [merged[i] for i in ordered]


def sparse_query_vector(text: str, idf: Callable[[int], float] | None = None) -> SparseVector:
    """Query terms weighted by ``idf`` (1.0 each if omitted); repeats do not count extra.

    The weight is the term's IDF in the repository being searched, supplied by the caller
    from ``TermStats``. Documents hold only the term-frequency half of BM25, so the dot
    product Qdrant computes is the full BM25 sum.
    """
    indices = sorted({term_index(t) for t in sparse_tokens(text)})
    return indices, [idf(i) if idf else 1.0 for i in indices]


def document_terms(text: str) -> set[int]:
    """Distinct term indices of a document: what its sparse vector's indices are."""
    return {term_index(t) for t in sparse_tokens(text)}


def payload_embedding_text(payload: Mapping[str, object]) -> str:
    """Rebuild a stored chunk's embedding text from its payload (same as ``embedding_text``).

    Used to recover the terms of a point that is about to be deleted, so the repository's
    document frequencies can be decremented exactly.
    """
    return f"{payload['path']}\n{payload['symbol']}\n{payload['text']}"


def embedding_text(chunk: Chunk) -> str:
    """SRS 4.3 step 6: prefix the chunk with its path and symbol before encoding.

    Both encoders see this, so a query for "retry" can match a function named ``retry``
    even when the body never says it, and a moved file is re-embedded (the text changed).
    """
    return f"{chunk.path}\n{chunk.symbol}\n{chunk.text}"
