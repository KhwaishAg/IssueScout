"""Measure chunk counts and sizes on real repositories (replaces SRS 6.6 estimates).

Chunks every indexable file of each dev-split repository at its first benchmark commit,
in memory only, and reports per-repository and overall figures for AST chunking (A1) and
line-window chunking (A0). Needs the mirrors made by `issuescout dataset prepare`.

Usage: uv run python scripts/measure_volumes.py
"""

import json
import statistics
from pathlib import Path

from issuescout.evaluation.gitstore import RepoMirror
from issuescout.indexing.chunker import chunk_file
from issuescout.indexing.filters import BINARY_SNIFF_BYTES, skip_reason
from issuescout.settings import get_settings

settings = get_settings()
rows = json.loads(Path("eval/instances_dev.json").read_text(encoding="utf-8"))
first = {r["repo"]: r["base_commit"] for r in rows if r["order_in_repo"] == 0}

grand = {"files": 0, "ast": 0, "lines": 0, "bytes": 0, "tokens": 0, "split": 0}
sizes: list[int] = []
tokens: list[int] = []
print(
    f"{'repository':32} {'files':>6} {'A1 chunks':>10} {'per file':>9} {'A0 chunks':>10} {'KB/chunk':>9}"
)
for repo, sha in sorted(first.items()):
    mirror = RepoMirror(
        settings.state_dir.resolve() / "swebench" / f"{repo.replace('/', '__')}.git"
    )
    entries = [
        e for e in mirror.list_tree(sha) if skip_reason(e.path, b"", e.size, settings) is None
    ]
    blobs = mirror.read_blobs([e.sha for e in entries])
    files = ast_n = line_n = 0
    for e in entries:
        data = blobs[e.sha]
        if skip_reason(e.path, data[:BINARY_SNIFF_BYTES], e.size, settings) is not None:
            continue
        files += 1
        ast_chunks = chunk_file(e.path, data, settings)
        ast_n += len(ast_chunks)
        line_n += len(chunk_file(e.path, data, settings, ast=False))
        for c in ast_chunks:
            sizes.append(len(c.text.encode("utf-8")))
            tokens.append(c.token_count)
            grand["split"] += c.part is not None
    kb = statistics.mean(sizes[-ast_n:]) / 1024 if ast_n else 0
    print(f"{repo:32} {files:>6} {ast_n:>10} {ast_n / files:>9.1f} {line_n:>10} {kb:>9.2f}")
    grand["files"] += files
    grand["ast"] += ast_n
    grand["lines"] += line_n

print()
print(
    f"total files {grand['files']}, A1 chunks {grand['ast']} ({grand['ast'] / grand['files']:.1f} per file), "
    f"A0 chunks {grand['lines']} ({grand['lines'] / grand['files']:.1f} per file)"
)
print(
    f"A1 chunk text: mean {statistics.mean(sizes) / 1024:.2f} KB, median {statistics.median(sizes) / 1024:.2f} KB, "
    f"p95 {sorted(sizes)[int(0.95 * len(sizes))] / 1024:.2f} KB"
)
print(
    f"A1 chunk tokens: mean {statistics.mean(tokens):.0f}, median {statistics.median(tokens):.0f}, "
    f"p95 {sorted(tokens)[int(0.95 * len(tokens))]}, split windows {grand['split']} "
    f"({100 * grand['split'] / grand['ast']:.1f}% of chunks)"
)
