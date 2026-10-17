"""P1.2 'Done when': embedding the same repository twice is near-instant the second time.

Usage: uv run python scripts/check_embedding_cache.py [repo_dir]   (default: src)
Set EMBEDDING_MODEL to pick the model; the cache goes in a throwaway directory so the
first pass is genuinely cold.
"""

import sys
import tempfile
import time
from pathlib import Path

from issuescout.indexing.chunker import chunk_file
from issuescout.indexing.dense import EmbeddingCache, SentenceTransformerEncoder, embed_texts
from issuescout.indexing.filters import select_files
from issuescout.indexing.sparse import embedding_text
from issuescout.settings import get_settings


def main() -> int:
    settings = get_settings()
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "src")
    chunks = [
        c for rel, data in select_files(root, settings) for c in chunk_file(rel, data, settings)
    ]
    texts = [embedding_text(c) for c in chunks]
    encoder = SentenceTransformerEncoder.from_settings(settings)
    print(f"model={encoder.model_id} chunks={len(chunks)}")

    with tempfile.TemporaryDirectory() as tmp:
        cache = EmbeddingCache(Path(tmp))
        encoder.encode(["warm up"])  # load weights outside the timed region
        t0 = time.perf_counter()
        first = embed_texts(texts, encoder, cache, settings.dense_batch_size)
        cold = time.perf_counter() - t0
        t0 = time.perf_counter()
        second = embed_texts(texts, encoder, cache, settings.dense_batch_size)
        warm = time.perf_counter() - t0
        cache.close()

    same = all((a == b).all() for a, b in zip(first, second, strict=True))
    rate = len(chunks) / cold
    print(f"cold={cold:.2f}s ({rate:.1f} chunks/s)  warm={warm:.3f}s  ratio={cold / warm:.0f}x")
    print(f"dim={len(first[0])}  identical_vectors={same}")
    if not same or warm > cold / 10:
        print("FAIL: second run was not at least 10x faster, or vectors differ")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
