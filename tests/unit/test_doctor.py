"""P0.5 checks: reachable services are reported ok, and a dead one is named."""

import json
import socket
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from issuescout import doctor
from issuescout.cli import app
from issuescout.settings import Settings

runner = CliRunner()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


def test_tcp_check_ok_and_fail() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert doctor.check_tcp("x", f"mongodb://127.0.0.1:{port}", 1).status == "ok"
    assert doctor.check_tcp("x", f"mongodb://127.0.0.1:{port}", 1).status == "fail"


def test_http_check_ok_and_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, timeout: float) -> httpx.Response:
        return httpx.Response(200 if url.endswith("/ok") else 503)

    monkeypatch.setattr(httpx, "get", fake_get)
    assert doctor.check_http("a", "http://h/ok").status == "ok"
    assert doctor.check_http("a", "http://h/bad").status == "fail"


def test_http_check_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", boom)
    assert doctor.check_http("qdrant", "http://h").status == "fail"


def test_model_cache_empty_warns_and_populated_is_ok(tmp_path: Path) -> None:
    assert doctor.check_model_cache(tmp_path).status == "warn"
    (tmp_path / "models--org--name").mkdir()
    assert doctor.check_model_cache(tmp_path).status == "ok"


def test_cli_names_qdrant_when_it_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    dead = _free_port()
    monkeypatch.setenv("QDRANT_URL", f"http://127.0.0.1:{dead}")
    monkeypatch.setattr(doctor, "check_tcp", lambda n, u, p: doctor.CheckResult(n, "ok", "stub"))
    monkeypatch.setattr(
        doctor,
        "check_http",
        lambda n, u: doctor.CheckResult(n, "fail" if n == "qdrant" else "ok", "stub"),
    )
    from issuescout.settings import get_settings

    get_settings.cache_clear()
    result = runner.invoke(app, ["doctor"])
    get_settings.cache_clear()
    assert result.exit_code == 1
    assert "[FAIL] qdrant" in result.output


def test_cli_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = doctor.CheckResult("svc", "ok", "stub")
    monkeypatch.setattr("issuescout.cli.run_checks", lambda s: [ok])
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True, "checks": [ok.as_dict()]}


def test_run_checks_covers_every_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "check_tcp", lambda n, u, p: doctor.CheckResult(n, "ok", ""))
    monkeypatch.setattr(doctor, "check_http", lambda n, u: doctor.CheckResult(n, "ok", ""))
    names = [r.name for r in doctor.run_checks(Settings(_env_file=None), Path("."))]
    assert names == ["qdrant", "mongodb", "rabbitmq", "mlflow", "model cache"]
