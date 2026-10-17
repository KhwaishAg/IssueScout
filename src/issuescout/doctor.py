"""Dependency checks behind ``issuescout doctor`` (FR-CLI-06).

Each check returns a ``CheckResult`` and never raises, so one dead service cannot hide
the state of the others. Services are probed at the protocol level (HTTP or a TCP
connect) so no database driver is needed until a package actually uses one.
"""

import os
import socket
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from issuescout.settings import Settings

TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # "ok" | "fail" | "warn"
    detail: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def check_http(name: str, url: str) -> CheckResult:
    try:
        response = httpx.get(url, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        return CheckResult(name, "fail", f"{url} unreachable: {type(exc).__name__}")
    if response.status_code >= 400:
        return CheckResult(name, "fail", f"{url} returned HTTP {response.status_code}")
    return CheckResult(name, "ok", f"{url} -> HTTP {response.status_code}")


def check_tcp(name: str, url: str, default_port: int) -> CheckResult:
    parsed = urlparse(url)
    host, port = parsed.hostname or "localhost", parsed.port or default_port
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS):
            pass
    except OSError as exc:
        return CheckResult(name, "fail", f"{host}:{port} unreachable: {type(exc).__name__}")
    return CheckResult(name, "ok", f"{host}:{port} accepts connections")


def model_cache_dir() -> Path:
    hf_home = os.environ.get("HF_HOME")
    base = Path(hf_home) if hf_home else Path.home() / ".cache" / "huggingface"
    return base / "hub"


def check_model_cache(cache_dir: Path | None = None) -> CheckResult:
    """Warn, not fail, on an empty cache: models are chosen in P2.2."""
    path = cache_dir if cache_dir is not None else model_cache_dir()
    models = sorted(p.name for p in path.glob("models--*")) if path.is_dir() else []
    if not models:
        return CheckResult("model cache", "warn", f"no models cached in {path}")
    return CheckResult("model cache", "ok", f"{len(models)} model(s) in {path}")


def run_checks(settings: Settings, cache_dir: Path | None = None) -> list[CheckResult]:
    qdrant = settings.qdrant_url.rstrip("/")
    mlflow = settings.mlflow_tracking_uri.rstrip("/")
    return [
        check_http("qdrant", f"{qdrant}/readyz"),
        check_tcp("mongodb", settings.mongodb_uri, 27017),
        check_tcp("rabbitmq", settings.rabbitmq_url, 5672),
        check_http("mlflow", f"{mlflow}/health"),
        check_model_cache(cache_dir),
    ]


def all_reachable(results: list[CheckResult]) -> bool:
    return all(r.status != "fail" for r in results)
