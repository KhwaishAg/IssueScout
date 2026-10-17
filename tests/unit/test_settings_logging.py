"""P0.2 'Done when': settings load from the environment, and a log line is valid JSON."""

import json

import pytest
import structlog
from pydantic import ValidationError

from issuescout.logging import configure_logging, correlation_id, get_correlation_id
from issuescout.settings import Settings, get_settings


def _load() -> Settings:
    # Ignore any developer .env file so tests see only defaults and the monkeypatched env.
    return Settings(_env_file=None)


def test_defaults_match_srs_appendix_b() -> None:
    s = _load()
    assert (s.dense_top_k, s.sparse_top_k, s.rerank_depth) == (50, 50, 30)
    assert (s.max_files, s.max_symbols_per_file) == (5, 3)
    assert (s.chunk_max_tokens, s.chunk_overlap_tokens) == (400, 60)
    assert (s.window_lines, s.window_overlap_lines) == (60, 10)
    assert (s.agg_alpha, s.history_beta) == (0.10, 0.15)
    assert (s.llm_timeout_seconds, s.llm_max_output_tokens) == (20, 500)
    assert s.max_comments_per_repo_per_day == 60
    assert s.max_answers_per_issue == 5
    assert s.max_file_bytes == 500_000
    assert s.max_repo_files == 20_000
    assert s.history_max_issues == 5_000
    assert s.dry_run is False
    assert s.conf_high is None
    assert s.conf_medium is None


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DENSE_TOP_K", "100")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    s = _load()
    assert s.dense_top_k == 100
    assert s.dry_run is True
    assert s.qdrant_url == "http://qdrant:6333"


def test_invalid_value_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DENSE_TOP_K", "0")
    with pytest.raises(ValidationError):
        _load()


def test_settings_are_immutable() -> None:
    s = _load()
    with pytest.raises(ValidationError):
        s.dense_top_k = 1  # type: ignore[misc]


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("MAX_FILES", "7")
    assert get_settings() is get_settings()
    assert get_settings().max_files == 7
    get_settings.cache_clear()


def test_log_line_is_valid_json_with_correlation_id(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    log = structlog.get_logger()
    with correlation_id("delivery-123"):
        log.info("job_started", repo_id=42)
    record = json.loads(capsys.readouterr().out.strip())
    assert record["event"] == "job_started"
    assert record["repo_id"] == 42
    assert record["level"] == "info"
    assert record["correlation_id"] == "delivery-123"
    assert "timestamp" in record


def test_correlation_id_is_null_outside_a_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger().info("no_context")
    assert json.loads(capsys.readouterr().out.strip())["correlation_id"] is None
    assert get_correlation_id() is None


def test_level_filters_debug_lines(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING")
    structlog.get_logger().info("hidden")
    assert capsys.readouterr().out == ""


def test_localhost_service_urls_are_rewritten_to_ipv4_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672//")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.internal:5000")
    s = _load()
    assert s.qdrant_url == "http://127.0.0.1:6333"
    assert s.rabbitmq_url == "amqp://guest:guest@127.0.0.1:5672//"
    assert s.mlflow_tracking_uri == "http://mlflow.internal:5000"  # real hostnames untouched
