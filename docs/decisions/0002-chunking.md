# 0002. Chunking strategy

Status: accepted (P0.7), to be re-checked by the chunk-size sensitivity study (P2.6)
Covers: SRS 4.3, FR-IDX-04, 08, 09, 10

## Context
Retrieval returns chunks, and the product shows a person a file, a function, and a line
range. A chunk should therefore be a unit a human can read and navigate to.

## Decision
- Parse with **tree-sitter**; emit one chunk per function or method, a class-header chunk
  (signature, docstring, method list), and one module chunk for remaining top-level code.
- Split any chunk over **400 tokens** into 400-token windows with **60 tokens** of overlap,
  each keeping the parent signature.
- Unsupported languages, docs, and files that fail to parse fall back to **60-line windows
  with 10 lines of overlap**; a parse failure is logged and never fails the job.
- Prefix each chunk's embedded text with its file path and qualified symbol name.

## Why
- A function is the unit in which a fix is made and the unit an explanation points to;
  fixed windows cut functions in half and give results nobody can navigate to.
- tree-sitter tolerates syntax errors and offers one interface for five languages;
  Python's `ast` handles one language and rejects broken files.
- The fallback guarantees every file is searchable even when parsing fails.
- The path/symbol prefix lets both the dense and sparse signals see names that do not
  appear in the body.

## Alternatives rejected
Fixed-size windows only (kept as the A0 ablation baseline, not the product); whole-file
chunks (too coarse, diluted embeddings); `ast` module (one language).

## Consequences
Chunk sizes (400/60, 60/10) are settings, not constants, so the P2.6 sensitivity study can
vary them. A0 vs A1 in the ablation measures what AST chunking is actually worth.