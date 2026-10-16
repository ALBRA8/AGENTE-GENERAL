"""Regression tests for API edge-control backstops (HTTPS/CORS/rate limits)."""
from __future__ import annotations

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path, *, read_limit="60", write_limit="10"):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ALBRA_PROVIDER_SETTINGS_TOKEN", "test-provider-settings-token-with-minimum-32-chars")
    monkeypatch.setenv("ALBRA_PROVIDER_CONFIG_DB", str(tmp_path / "edge-controls.db"))
    monkeypatch.setenv("ALBRA_PROVIDER_RATE_LIMIT_PER_MINUTE", read_limit)
    monkeypatch.setenv("ALBRA_PROVIDER_WRITE_RATE_LIMIT_PER_MINUTE", write_limit)
    monkeypatch.delenv("ALBRA_CORS_ORIGINS", raising=False)

    from albra_seed import Runtime, SeedConfig, create_app

    app = create_app(Runtime(config=SeedConfig()))
    return TestClient(
        app,
        headers={"Authorization": "Bearer test-provider-settings-token-with-minimum-32-chars"},
    )


def test_provider_settings_read_rate_limit_returns_429_and_retry_after(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, read_limit="2")
    assert client.get("/api/v1/provider/config").status_code == 200
    assert client.get("/api/v1/provider/config").status_code == 200
    response = client.get("/api/v1/provider/config")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["Cache-Control"] == "no-store"
    assert "test-provider-settings-token" not in response.text


def test_provider_settings_write_rate_limit_is_separate(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, read_limit="20", write_limit="1")
    payload = {"provider": "mock", "base_url": "", "model": ""}
    assert client.put("/api/v1/provider/config", json=payload).status_code == 200
    response = client.put("/api/v1/provider/config", json=payload)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_oversized_declared_body_rejected_before_route(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.put(
        "/api/v1/provider/config",
        content=b'{}',
        headers={"Content-Type": "application/json", "Content-Length": "1048577"},
    )
    assert response.status_code == 413


def test_oversized_body_without_content_length_is_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.put(
        "/api/v1/provider/config",
        content=b"x" * 1_048_577,
        headers={"Content-Type": "application/octet-stream", "Content-Length": ""},
    )
    assert response.status_code == 413


def test_cors_is_disabled_by_default(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.options(
        "/api/v1/provider/config",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_rejects_non_https_non_local_origins(monkeypatch):
    monkeypatch.setenv("ALBRA_CORS_ORIGINS", "http://dashboard.example.com")
    from albra_seed.api.app import create_app
    from albra_seed import Runtime, SeedConfig
    import pytest

    with pytest.raises(ValueError, match="exact HTTPS origins"):
        create_app(Runtime(config=SeedConfig()))


def test_invalid_rate_limit_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv("ALBRA_PROVIDER_RATE_LIMIT_PER_MINUTE", "0")
    from albra_seed.api.app import create_app
    from albra_seed import Runtime, SeedConfig
    import pytest

    with pytest.raises(ValueError, match="positive integer"):
        create_app(Runtime(config=SeedConfig()))
