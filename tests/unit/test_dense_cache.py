"""P1.2: batching and the on-disk embedding cache (risk R-02)."""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from issuescout.indexing.dense import EmbeddingCache, Vector, cache_key, embed_texts


class FakeEncoder:
    """Deterministic stand-in for a model; records every batch it is asked to encode."""

    def __init__(self, model_id: str = "fake@1") -> None:
        self._model_id = model_id
        self.batches: list[list[str]] = []

    @property
    def model_id(self) -> str:
        return self._model_id

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        self.batches.append(list(texts))
        return [np.full(4, float(len(t)), dtype=np.float32) for t in texts]

    @property
    def calls(self) -> int:
        return sum(len(b) for b in self.batches)


def test_second_run_encodes_nothing(tmp_path: Path) -> None:
    texts = [f"chunk {i}" for i in range(100)]
    encoder, cache = FakeEncoder(), EmbeddingCache(tmp_path)
    first = embed_texts(texts, encoder, cache)
    assert encoder.calls == 100
    second = embed_texts(texts, encoder, cache)
    assert encoder.calls == 100  # nothing new was encoded
    assert all(np.array_equal(a, b) for a, b in zip(first, second, strict=True))
    assert (cache.hits, cache.misses) == (100, 100)


def test_cache_survives_reopening(tmp_path: Path) -> None:
    first_cache = EmbeddingCache(tmp_path)
    embed_texts(["a", "b"], FakeEncoder(), first_cache)
    first_cache.close()
    encoder = FakeEncoder()
    embed_texts(["a", "b"], encoder, EmbeddingCache(tmp_path))
    assert encoder.calls == 0


def test_only_changed_texts_are_encoded(tmp_path: Path) -> None:
    encoder, cache = FakeEncoder(), EmbeddingCache(tmp_path)
    embed_texts(["one", "two", "three"], encoder, cache)
    embed_texts(["one", "TWO changed", "three"], encoder, cache)
    assert encoder.batches[-1] == ["TWO changed"]


def test_different_model_or_revision_does_not_share_entries(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path)
    embed_texts(["x"], FakeEncoder("model-a@1"), cache)
    other = FakeEncoder("model-a@2")
    embed_texts(["x"], other, cache)
    assert other.calls == 1
    assert cache_key("model-a@1", "x") != cache_key("model-a@2", "x")


def test_batches_respect_batch_size(tmp_path: Path) -> None:
    encoder = FakeEncoder()
    embed_texts([f"t{i}" for i in range(70)], encoder, EmbeddingCache(tmp_path), batch_size=32)
    assert [len(b) for b in encoder.batches] == [32, 32, 6]


def test_duplicates_are_encoded_once_and_order_is_kept(tmp_path: Path) -> None:
    encoder = FakeEncoder()
    out = embed_texts(["aa", "b", "aa", "ccc"], encoder, EmbeddingCache(tmp_path))
    assert encoder.calls == 3
    assert [float(v[0]) for v in out] == [2.0, 1.0, 2.0, 3.0]


def test_partial_progress_is_saved_for_resume(tmp_path: Path) -> None:
    """An interrupted run keeps the batches it finished (groundwork for FR-IDX-15)."""

    class Boom(FakeEncoder):
        def encode(self, texts: Sequence[str]) -> list[Vector]:
            if len(self.batches) == 1:
                raise RuntimeError("killed")
            return super().encode(texts)

    cache = EmbeddingCache(tmp_path)
    texts = [f"t{i}" for i in range(10)]
    with pytest.raises(RuntimeError):
        embed_texts(texts, Boom(), cache, batch_size=4)
    assert len(cache) == 4  # first batch survived
    encoder = FakeEncoder()
    embed_texts(texts, encoder, cache, batch_size=4)
    assert encoder.calls == 6


def test_empty_input(tmp_path: Path) -> None:
    assert embed_texts([], FakeEncoder(), EmbeddingCache(tmp_path)) == []


def test_vectors_roundtrip_as_float32(tmp_path: Path) -> None:
    cache = EmbeddingCache(tmp_path)
    vec = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    cache.put_many({"k": vec})
    got = cache.get_many(["k", "missing"])
    assert set(got) == {"k"}
    assert got["k"].dtype == np.float32
    assert np.array_equal(got["k"], vec)
