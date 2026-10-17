"""GitHub credentials are optional, secret-typed, and never leak through repr."""

import pytest

from issuescout.settings import Settings


def test_secrets_default_to_none() -> None:
    s = Settings(_env_file=None)
    assert s.github_app_id is None
    assert s.github_app_private_key is None
    assert s.github_webhook_secret is None


def test_secrets_load_from_env_and_are_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cret-value")
    s = Settings(_env_file=None)
    assert s.github_app_id == 12345
    assert s.github_webhook_secret is not None
    assert s.github_webhook_secret.get_secret_value() == "s3cret-value"
    assert "s3cret-value" not in repr(s)
    assert "s3cret-value" not in str(s.model_dump())
