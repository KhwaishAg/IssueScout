"""Dense encoding with an on-disk embedding cache (SRS 4.3 step 7; risk R-02).

Embedding dominates indexing time, and the benchmark indexes the same repository at many
nearby commits where almost every chunk is unchanged. The cache is keyed by a hash of
(model, revision, text), so an unchanged chunk is never embedded twice, across runs and
across commits. A different model or revision gets different keys, so results never mix.
"""

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
import structlog

from issuescout.settings import Settings

log = structlog.get_logger()

Vector = npt.NDArray[np.float32]


class DenseEncoder(Protocol):
    """Anything that turns texts into unit-length vectors. Replaceable by configuration."""

    @property
    def model_id(self) -> str:
        """Model name plus revision; part of every cache key."""
        ...

    def encode(self, texts: Sequence[str]) -> list[Vector]: ...


class SentenceTransformerEncoder:
    """sentence-transformers model on CPU, loaded on first use."""

    def __init__(
        self,
        model_name: str,
        revision: str | None = None,
        batch_size: int = 32,
        max_seq_length: int = 512,
    ) -> None:
        self.model_name = model_name
        self.revision = revision
        self.batch_size = batch_size
        self.max_seq_length = max_seq_length
        self._model: Any = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "SentenceTransformerEncoder":
        return cls(
            settings.embedding_model,
            settings.embedding_revision,
            settings.dense_batch_size,
            settings.embedding_max_seq_length,
        )

    @property
    def model_id(self) -> str:
        return f"{self.model_name}@{self.revision or 'unpinned'}"

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            # No trust_remote_code: model repositories ship Python that would run on load.
            model = SentenceTransformer(
                self.model_name, revision=self.revision, device="cpu", trust_remote_code=False
            )
            model.max_seq_length = self.max_seq_length
            self._model = model
            log.info("encoder_loaded", model=self.model_id)
        return self._model

    def encode(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,  # cosine distance in Qdrant, so unit length
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [np.asarray(v, dtype=np.float32) for v in vectors]


def cache_key(model_id: str, text: str) -> str:
    return hashlib.sha256(f"{model_id}\n{text}".encode()).hexdigest()


class EmbeddingCache:
    """Key to float32 vector, in one SQLite file. Safe to delete at any time."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(directory / "embeddings.sqlite")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vec BLOB)")
        self.hits = 0
        self.misses = 0

    def get_many(self, keys: Sequence[str]) -> dict[str, Vector]:
        found: dict[str, Vector] = {}
        for start in range(0, len(keys), 500):  # stay under SQLite's variable limit
            batch = keys[start : start + 500]
            marks = ",".join("?" * len(batch))
            rows = self._db.execute(
                f"SELECT key, vec FROM vectors WHERE key IN ({marks})",
                batch,
            )
            for key, blob in rows:
                found[key] = np.frombuffer(blob, dtype=np.float32)
        return found

    def put_many(self, items: dict[str, Vector]) -> None:
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO vectors (key, vec) VALUES (?, ?)",
                [(k, v.astype(np.float32).tobytes()) for k, v in items.items()],
            )

    def __len__(self) -> int:
        row = self._db.execute("SELECT COUNT(*) FROM vectors").fetchone()
        return int(row[0])

    def close(self) -> None:
        self._db.close()


def embed_texts(
    texts: Sequence[str],
    encoder: DenseEncoder,
    cache: EmbeddingCache,
    batch_size: int = 32,
) -> list[Vector]:
    """Vectors for ``texts`` in order. Cached texts are never re-encoded.

    Duplicate texts within one call are encoded once.
    """
    model_id = encoder.model_id
    keys = [cache_key(model_id, t) for t in texts]
    cached = cache.get_many(list(dict.fromkeys(keys)))
    missing = list(dict.fromkeys(k for k in keys if k not in cached))
    text_for_key = dict(zip(keys, texts, strict=True))

    fresh: dict[str, Vector] = {}
    for start in range(0, len(missing), batch_size):
        batch_keys = missing[start : start + batch_size]
        vectors = encoder.encode([text_for_key[k] for k in batch_keys])
        fresh.update(zip(batch_keys, vectors, strict=True))
        cache.put_many(dict(zip(batch_keys, vectors, strict=True)))  # resumable if interrupted

    cache.hits += sum(1 for k in keys if k in cached)
    cache.misses += sum(1 for k in keys if k not in cached)
    lookup = {**cached, **fresh}
    return [lookup[k] for k in keys]
