"""Typed settings: every tunable value lives here (NFR-MNT-04).

Field names are the lowercase form of the SRS Appendix B names, so the environment
variable ``DENSE_TOP_K`` sets ``Settings.dense_top_k``. Values are read from the
environment and from an ignored ``.env`` file; nothing secret has a default.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # Retrieval (Appendix B)
    dense_top_k: int = Field(50, gt=0, description="Dense candidates per query")
    sparse_top_k: int = Field(50, gt=0, description="Sparse candidates per query")
    rerank_depth: int = Field(30, gt=0, description="Fused candidates passed to the reranker")
    max_files: int = Field(5, gt=0, description="Files shown in a comment")
    max_symbols_per_file: int = Field(3, gt=0, description="Symbols shown per file")
    agg_alpha: float = Field(0.10, ge=0, description="Credit for further chunks in a file")
    history_beta: float = Field(0.15, ge=0, description="Weight of the history term")
    # Calibrated in Phase 2 (P2.5); unset until then.
    conf_high: float | None = None
    conf_medium: float | None = None

    # Chunking (Appendix B)
    chunk_max_tokens: int = Field(400, gt=0, description="Largest chunk before splitting")
    chunk_overlap_tokens: int = Field(60, ge=0, description="Overlap between split windows")
    window_lines: int = Field(60, gt=0, description="Line-window size for fallback chunking")
    window_overlap_lines: int = Field(10, ge=0, description="Overlap between line windows")

    # Encoders (SRS 4.3 step 7, 5.2). The model is a provisional baseline: P2.2 picks the real
    # one by measurement. Qwen3-Embedding-0.6B (an SRS shortlist model) was tried first and
    # managed well under 1 chunk/s on CPU, so it cannot meet the indexing-speed target here.
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_revision: str | None = None  # pin to a commit hash once P2.2 chooses a model
    embedding_max_seq_length: int = Field(512, gt=0, description="Token cap per chunk")
    dense_batch_size: int = Field(32, gt=0, description="Chunks per encoder batch")
    embedding_cache_dir: Path = Path(".embedding_cache")
    # Sparse term weighting (BM25 TF part; Qdrant applies IDF). avgdl is a placeholder
    # until P1.7 measures the real mean chunk length.
    sparse_k1: float = Field(1.2, gt=0)
    sparse_b: float = Field(0.75, ge=0, le=1)
    sparse_avgdl: float = Field(120.0, gt=0)

    # Generation (Appendix B)
    llm_timeout_seconds: int = Field(20, gt=0, description="Hard limit on generation")
    llm_max_output_tokens: int = Field(500, gt=0, description="Output cap per response")

    # Limits (Appendix B)
    max_comments_per_repo_per_day: int = Field(60, gt=0)
    max_answers_per_issue: int = Field(5, gt=0)
    max_file_bytes: int = Field(500_000, gt=0, description="Largest file indexed")
    max_repo_files: int = Field(20_000, gt=0, description="Largest repository accepted")
    history_max_issues: int = Field(5_000, gt=0, description="Closed issues ingested per repo")

    # Safety switch (FR-OPS-07): process events fully, post nothing.
    dry_run: bool = False

    # Service endpoints; defaults match the local Docker Compose stack (P0.3).
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "code_chunks"  # one collection per ablation configuration
    mongodb_uri: str = "mongodb://127.0.0.1:27017"
    mongodb_database: str = "issuescout"
    rabbitmq_url: str = "amqp://guest:guest@127.0.0.1:5672//"
    mlflow_tracking_uri: str = "http://127.0.0.1:5000"

    # GitHub App credentials (FR-APP-07/08). No defaults; SecretStr keeps them out of
    # reprs and logs. Supplied only through the environment or the ignored .env file.
    github_app_id: int | None = None
    github_app_private_key: SecretStr | None = None
    github_webhook_secret: SecretStr | None = None

    # SWE-bench: bare mirrors of the benchmark repositories live under state_dir/swebench.
    # Local state: clones of remote repositories for the CLI.
    state_dir: Path = Path(".issuescout")

    @field_validator("qdrant_url", "mongodb_uri", "rabbitmq_url", "mlflow_tracking_uri")
    @classmethod
    def _use_ipv4_loopback(cls, value: str) -> str:
        """Rewrite a ``localhost`` host to ``127.0.0.1``.

        On Windows, ``localhost`` resolves to IPv6 first; the services listen on IPv4
        only, so every new connection waited about a second for the IPv6 attempt to time
        out before falling back. That made each Qdrant call take about 2 s. The loopback
        address is the same machine without the delay.
        """
        return value.replace("//localhost", "//127.0.0.1").replace("@localhost", "@127.0.0.1")

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process. Tests call ``get_settings.cache_clear()``."""
    return Settings()
