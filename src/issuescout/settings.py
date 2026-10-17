"""Typed settings: every tunable value lives here (NFR-MNT-04).

Field names are the lowercase form of the SRS Appendix B names, so the environment
variable ``DENSE_TOP_K`` sets ``Settings.dense_top_k``. Values are read from the
environment and from an ignored ``.env`` file; nothing secret has a default.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
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
    qdrant_url: str = "http://localhost:6333"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "issuescout"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672//"
    mlflow_tracking_uri: str = "http://localhost:5000"

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process. Tests call ``get_settings.cache_clear()``."""
    return Settings()
