"""Baseline retrieval: one signal (sparse or dense), chunks rolled up to files.

This is the A0-A2 baseline of the ablation (SRS 10.5). It deliberately does nothing clever:
fusion, reranking, the SRS 4.5 file score and history all arrive in Phase 2, so their gain
can be measured against this.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from issuescout.indexing.dense import DenseEncoder
from issuescout.indexing.pipeline import load_termstats
from issuescout.indexing.sparse import sparse_query_vector
from issuescout.settings import Settings
from issuescout.storage.qdrant import Hit, QdrantClient, search_code

Mode = Literal["sparse", "dense"]


@dataclass(frozen=True)
class SymbolRef:
    symbol: str
    kind: str
    start_line: int
    end_line: int
    score: float


@dataclass(frozen=True)
class RankedFile:
    path: str
    score: float  # the best chunk score in the file
    symbols: tuple[SymbolRef, ...]


def rank_files(hits: Sequence[Hit], max_symbols: int) -> list[RankedFile]:
    """Group chunk hits by file. A file scores as its best chunk; ties break on path.

    Each file keeps its best ``max_symbols`` distinct symbols (FR-RET-08). Windows of one
    split function count as a single symbol, represented by the highest-scoring window.
    Ranking is deterministic for a given hit list (FR-RET-12).
    """
    by_file: dict[str, list[Hit]] = {}
    for hit in hits:
        by_file.setdefault(hit.path, []).append(hit)

    ranked: list[RankedFile] = []
    for path, file_hits in by_file.items():
        best_first = sorted(file_hits, key=lambda h: (-h.score, h.start_line, h.symbol))
        symbols: list[SymbolRef] = []
        seen: set[str] = set()
        for h in best_first:
            if h.symbol in seen:
                continue
            seen.add(h.symbol)
            symbols.append(SymbolRef(h.symbol, h.kind, h.start_line, h.end_line, h.score))
            if len(symbols) == max_symbols:
                break
        ranked.append(RankedFile(path, best_first[0].score, tuple(symbols)))
    return sorted(ranked, key=lambda f: (-f.score, f.path))


def retrieve_hits(
    query: str,
    *,
    client: QdrantClient,
    repo_id: int,
    index_version: int,
    mode: Mode,
    settings: Settings,
    encoder: DenseEncoder | None = None,
    exclude_tests: bool = False,
    collection: str | None = None,
) -> list[Hit]:
    """Retrieve chunks with one signal, best first."""
    collection = collection or settings.qdrant_collection
    if mode == "dense":
        if encoder is None:
            raise ValueError("dense mode needs an encoder")
        return search_code(
            client,
            repo_id=repo_id,
            index_version=index_version,
            dense=[float(x) for x in encoder.encode([query])[0]],
            limit=settings.dense_top_k,
            exclude_tests=exclude_tests,
            collection=collection,
        )
    # IDF comes from this repository's own statistics, not from the collection (see
    # indexing/termstats.py), so the ranking cannot depend on what else is indexed.
    termstats = load_termstats(settings, client, repo_id, collection)
    return search_code(
        client,
        repo_id=repo_id,
        index_version=index_version,
        sparse=sparse_query_vector(query, termstats.idf),
        limit=settings.sparse_top_k,
        exclude_tests=exclude_tests,
        collection=collection,
    )


def rank_functions(hits: Sequence[Hit]) -> list[str]:
    """``path::symbol`` of each function or method hit, best score first, no repeats.

    Class headers, module chunks and line windows are not functions, so they are skipped.
    Windows of one split function share a symbol and count once. Same name format as the
    gold functions of the evaluation, so the two lists compare directly.
    """
    ordered = sorted(hits, key=lambda h: (-h.score, h.path, h.start_line))
    seen: dict[str, None] = {}
    for h in ordered:
        if h.kind in ("function", "method"):
            seen.setdefault(f"{h.path}::{h.symbol}", None)
    return list(seen)


def search_files(
    query: str,
    *,
    client: QdrantClient,
    repo_id: int,
    index_version: int,
    mode: Mode,
    settings: Settings,
    encoder: DenseEncoder | None = None,
    exclude_tests: bool = False,
    collection: str | None = None,
) -> list[RankedFile]:
    """Retrieve chunks with one signal and return ranked files."""
    hits = retrieve_hits(
        query,
        client=client,
        repo_id=repo_id,
        index_version=index_version,
        mode=mode,
        settings=settings,
        encoder=encoder,
        exclude_tests=exclude_tests,
        collection=collection,
    )
    return rank_files(hits, settings.max_symbols_per_file)
