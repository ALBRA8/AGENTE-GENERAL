"""Tests for the provider settings store + endpoints.

Verifies:
- Config save/load (non-sensitive fields).
- API key save/load (encrypted at rest).
- API key preservation when config is updated without a new key.
- API key NOT returned by get_config (only api_key_set: bool).
- Encryption failure (missing SETTINGS_ENCRYPTION_KEY) → fail-closed.
- Reset to defaults.
- Provider-settings endpoints require the configured bearer token.
- Endpoint request validation.
- No secrets in responses.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def temp_env(monkeypatch, tmp_path):
    """Set up a clean environment with a valid encryption key + temp DB."""
    key = Fernet.generate_key().decode()
    db_path = tmp_path / "test_provider_config.db"
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", key)
    monkeypatch.setenv("ALBRA_PROVIDER_SETTINGS_TOKEN", "test-provider-settings-token-with-minimum-32-chars")
    monkeypatch.setenv("ALBRA_PROVIDER_CONFIG_DB", str(db_path))
    yield {"key": key, "db_path": db_path}


# =====================================================================
# Settings store unit tests
# =====================================================================


class TestSettingsStore:
    """Verify the settings_store module."""

    def test_default_config(self, temp_env):
        from albra_seed.config.settings_store import get_config

        cfg = get_config()
        assert cfg["provider"] == "mock"
        assert cfg["api_key_set"] is False
        assert cfg["last_test_at"] is None

    def test_save_and_load_config(self, temp_env):
        from albra_seed.config.settings_store import get_config, save_config

        save_config(
            provider="nvidia",
            base_url="https://integrate.api.nvidia.com/v1",
            model="z-ai/glm-5.3",
            temperature=0.7,
            top_p=0.9,
            max_tokens=2048,
            timeout=30.0,
            stream=True,
        )

        cfg = get_config()
        assert cfg["provider"] == "nvidia"
        assert cfg["base_url"] == "https://integrate.api.nvidia.com/v1"
        assert cfg["model"] == "z-ai/glm-5.3"
        assert cfg["temperature"] == 0.7
        assert cfg["top_p"] == 0.9
        assert cfg["max_tokens"] == 2048
        assert cfg["timeout"] == 30.0
        assert cfg["stream"] is True
        assert cfg["api_key_set"] is False

    def test_save_api_key_and_read_back(self, temp_env):
        from albra_seed.config.settings_store import get_api_key, save_api_key

        save_api_key("nvapi-test-secret-key-1234567890")

        key = get_api_key()
        assert key == "nvapi-test-secret-key-1234567890"

    def test_api_key_not_in_get_config(self, temp_env):
        """get_config() must NOT include the API key — only api_key_set."""
        from albra_seed.config.settings_store import get_config, save_api_key

        save_api_key("nvapi-test-secret-key-1234567890")

        cfg = get_config()
        assert "api_key" not in cfg
        assert cfg["api_key_set"] is True

    def test_api_key_preserved_on_config_update(self, temp_env):
        """If config is saved without a new key, the old key is preserved."""
        from albra_seed.config.settings_store import (
            get_api_key,
            get_config,
            save_api_key,
            save_config,
        )

        save_config(
            provider="nvidia",
            base_url="https://integrate.api.nvidia.com/v1",
            model="z-ai/glm-5.3",
        )
        save_api_key("nvapi-original-key-1234567890")

        # Re-save config without touching the key
        save_config(
            provider="nvidia",
            base_url="https://integrate.api.nvidia.com/v1",
            model="z-ai/glm-5.4",  # changed model
        )

        cfg = get_config()
        assert cfg["model"] == "z-ai/glm-5.4"
        assert cfg["api_key_set"] is True

        key = get_api_key()
        assert key == "nvapi-original-key-1234567890"

    def test_clear_api_key(self, temp_env):
        from albra_seed.config.settings_store import (
            clear_api_key,
            get_api_key,
            get_config,
            save_api_key,
        )

        save_api_key("nvapi-test-key")
        clear_api_key()

        assert get_api_key() is None
        cfg = get_config()
        assert cfg["api_key_set"] is False

    def test_reset_to_defaults(self, temp_env):
        from albra_seed.config.settings_store import (
            get_config,
            reset_to_defaults,
            save_api_key,
            save_config,
        )

        save_config(provider="nvidia", base_url="https://integrate.api.nvidia.com/v1", model="y")
        save_api_key("nvapi-test-key")
        reset_to_defaults()

        cfg = get_config()
        assert cfg["provider"] == "mock"
        assert cfg["api_key_set"] is False

    def test_record_test_result(self, temp_env):
        from albra_seed.config.settings_store import (
            get_config,
            record_test_result,
        )

        record_test_result(True)
        cfg = get_config()
        assert cfg["last_test_ok"] is True
        assert cfg["last_test_at"] is not None

        record_test_result(False)
        cfg = get_config()
        assert cfg["last_test_ok"] is False

    def test_missing_encryption_key_fails_closed(self, monkeypatch, tmp_path):
        """Without SETTINGS_ENCRYPTION_KEY, save_api_key must raise."""
        monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv(
            "ALBRA_PROVIDER_CONFIG_DB", str(tmp_path / "test.db")
        )

        from albra_seed.config.settings_store import (
            SettingsEncryptionError,
            save_api_key,
        )

        with pytest.raises(SettingsEncryptionError):
            save_api_key("nvapi-test-key")

    def test_get_api_key_returns_none_without_encryption_key(
        self, monkeypatch, tmp_path
    ):
        """get_api_key returns None if decryption is impossible."""
        monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv(
            "ALBRA_PROVIDER_CONFIG_DB", str(tmp_path / "test.db")
        )

        from albra_seed.config.settings_store import get_api_key

        assert get_api_key() is None

    def test_invalid_provider_rejected(self, temp_env):
        from albra_seed.config.settings_store import (
            ProviderConfigError,
            save_config,
        )

        with pytest.raises(ProviderConfigError, match="Unknown provider"):
            save_config(provider="invalid", base_url="x", model="y")

    def test_invalid_temperature_rejected(self, temp_env):
        from albra_seed.config.settings_store import (
            ProviderConfigError,
            save_config,
        )

        with pytest.raises(ProviderConfigError, match="temperature"):
            save_config(
                provider="nvidia",
                base_url="x",
                model="y",
                temperature=5.0,
            )

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://integrate.api.nvidia.com/v1",
            "https://127.0.0.1/v1",
            "https://localhost/v1",
            "https://169.254.169.254/latest/meta-data",
            "https://example.com/v1",
            "https://integrate.api.nvidia.com.evil.example/v1",
            "https://user:pass@integrate.api.nvidia.com/v1",
            "https://integrate.api.nvidia.com:8443/v1",
            "https://integrate.api.nvidia.com/v1?next=http://127.0.0.1",
        ],
    )
    def test_rejects_untrusted_provider_base_urls(self, temp_env, base_url):
        from albra_seed.config.settings_store import ProviderConfigError, save_config

        with pytest.raises(ProviderConfigError, match="base_url"):
            save_config(provider="nvidia", base_url=base_url, model="test-model")

    def test_db_file_permissions(self, temp_env):
        """DB file should be mode 0600 on POSIX."""
        from albra_seed.config.settings_store import save_config

        save_config(provider="mock", base_url="", model="")

        db_path = temp_env["db_path"]
        if os.name == "posix":
            mode = oct(db_path.stat().st_mode & 0o777)
            assert mode == "0o600", f"DB file mode is {mode}, expected 0o600"

    def test_symlink_db_path_rejected(self, temp_env, tmp_path, monkeypatch):
        """Reject symlink paths before SQLite can follow them."""
        from albra_seed.config.settings_store import ProviderConfigError
        import albra_seed.config.settings_store as store

        real_db = tmp_path / "real.db"
        symlink_db = tmp_path / "symlink.db"
        symlink_db.symlink_to(real_db)

        monkeypatch.setattr(store, "_db_path", lambda: symlink_db)

        def forbidden_connect(*args, **kwargs):
            pytest.fail("SQLite must not open a symlinked database path")

        monkeypatch.setattr(store.sqlite3, "connect", forbidden_connect)

        with pytest.raises(ProviderConfigError, match="symlink"):
            store._ensure_db()


# =====================================================================
# Endpoint integration tests (using FastAPI TestClient)
# =====================================================================


class TestProviderSettingsEndpoints:
    """Verify the /api/v1/provider/* endpoints."""

    @pytest.fixture
    def client(self, monkeypatch, tmp_path):
        """Each test gets a fresh DB + encryption key + fresh app."""
        key = Fernet.generate_key().decode()
        db_path = tmp_path / "endpoint_test.db"
        monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", key)
        monkeypatch.setenv("ALBRA_PROVIDER_SETTINGS_TOKEN", "test-provider-settings-token-with-minimum-32-chars")
        monkeypatch.setenv("ALBRA_PROVIDER_CONFIG_DB", str(db_path))

        # Force re-import of all affected modules so env vars are picked up
        import importlib
        import albra_seed.config.settings_store
        importlib.reload(albra_seed.config.settings_store)
        import albra_seed.api.routes.provider_settings
        importlib.reload(albra_seed.api.routes.provider_settings)

        from albra_seed import Runtime, SeedConfig, create_app
        from fastapi.testclient import TestClient

        runtime = Runtime(config=SeedConfig())
        app = create_app(runtime)
        return TestClient(
            app,
            headers={"Authorization": "Bearer test-provider-settings-token-with-minimum-32-chars"},
        )

    def test_rejects_missing_or_invalid_bearer_token(self, client):
        missing = client.get(
            "/api/v1/provider/config",
            headers={"Authorization": ""},
        )
        assert missing.status_code == 401

        invalid = client.get(
            "/api/v1/provider/config",
            headers={"Authorization": "Bearer incorrect-token"},
        )
        assert invalid.status_code == 401
        assert invalid.headers.get("www-authenticate") == "Bearer"

    def test_every_provider_endpoint_requires_authentication(self, client):
        requests = [
            ("GET", "/api/v1/provider/config", None),
            ("PUT", "/api/v1/provider/config", {
                "provider": "mock", "base_url": "", "model": ""
            }),
            ("PUT", "/api/v1/provider/api-key", {"api_key": "test-key"}),
            ("DELETE", "/api/v1/provider/api-key", None),
            ("POST", "/api/v1/provider/test", None),
            ("POST", "/api/v1/provider/reset", None),
            ("GET", "/api/v1/provider/defaults", None),
        ]
        for method, path, body in requests:
            response = client.request(
                method, path, json=body, headers={"Authorization": ""}
            )
            assert response.status_code == 401, f"{method} {path} bypassed auth"

    def test_fails_closed_when_token_is_not_configured(self, client, monkeypatch):
        monkeypatch.delenv("ALBRA_PROVIDER_SETTINGS_TOKEN")
        r = client.get("/api/v1/provider/config")
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]

    def test_fails_closed_when_token_is_too_short(self, client, monkeypatch):
        monkeypatch.setenv("ALBRA_PROVIDER_SETTINGS_TOKEN", "short")
        r = client.get("/api/v1/provider/config")
        assert r.status_code == 503
        assert "securely" in r.json()["detail"]

    def test_get_config_default(self, client):
        r = client.get("/api/v1/provider/config")
        assert r.status_code == 200
        data = r.json()
        assert data["provider"] == "mock"
        assert data["api_key_set"] is False
        assert "api_key" not in data

    def test_put_config(self, client):
        r = client.put("/api/v1/provider/config", json={
            "provider": "nvidia",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model": "z-ai/glm-5.3",
            "temperature": 0.5,
            "top_p": 1.0,
            "max_tokens": 1024,
            "timeout": 60.0,
            "stream": False,
        })
        assert r.status_code == 200
        data = r.json()
        assert data["provider"] == "nvidia"
        assert data["model"] == "z-ai/glm-5.3"

    def test_put_config_invalid_provider(self, client):
        r = client.put("/api/v1/provider/config", json={
            "provider": "invalid",
            "base_url": "",
            "model": "",
        })
        assert r.status_code == 400

    def test_put_config_rejects_ssrf_base_url(self, client):
        r = client.put("/api/v1/provider/config", json={
            "provider": "nvidia",
            "base_url": "http://127.0.0.1:8000/admin",
            "model": "test-model",
        })
        assert r.status_code == 400
        assert "base_url" in r.json()["detail"]

    def test_put_config_invalid_temperature(self, client):
        r = client.put("/api/v1/provider/config", json={
            "provider": "mock",
            "base_url": "",
            "model": "",
            "temperature": 5.0,
        })
        assert r.status_code == 422  # Pydantic validation

    def test_put_config_rejects_oversized_fields(self, client):
        oversized_url = client.put("/api/v1/provider/config", json={
            "provider": "nvidia",
            "base_url": "https://integrate.api.nvidia.com/v1" + ("a" * 2048),
            "model": "test-model",
        })
        assert oversized_url.status_code == 422

        oversized_model = client.put("/api/v1/provider/config", json={
            "provider": "mock",
            "base_url": "",
            "model": "m" * 257,
        })
        assert oversized_model.status_code == 422

    def test_put_api_key_rejects_oversized_secret(self, client):
        response = client.put(
            "/api/v1/provider/api-key",
            json={"api_key": "k" * 4097},
        )
        assert response.status_code == 422
        assert "k" * 64 not in response.text

    def test_put_api_key(self, client):
        r = client.put("/api/v1/provider/api-key", json={
            "api_key": "nvapi-test-secret-key"
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

        # Verify key is NOT in config response
        r2 = client.get("/api/v1/provider/config")
        assert r2.status_code == 200
        assert r2.json()["api_key_set"] is True
        assert "api_key" not in r2.json()

    def test_delete_api_key(self, client):
        # Set key first
        client.put("/api/v1/provider/api-key", json={"api_key": "nvapi-test"})
        # Delete it
        r = client.delete("/api/v1/provider/api-key")
        assert r.status_code == 200
        # Verify it's gone
        r2 = client.get("/api/v1/provider/config")
        assert r2.json()["api_key_set"] is False

    def test_test_connection_mock(self, client):
        """Mock provider always passes the test."""
        r = client.post("/api/v1/provider/test")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "Mock" in data["message"]

    def test_test_connection_nvidia_without_key(self, client):
        """NVIDIA without API key → 400."""
        client.put("/api/v1/provider/config", json={
            "provider": "nvidia",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model": "z-ai/glm-5.3",
        })
        r = client.post("/api/v1/provider/test")
        assert r.status_code == 400
        assert "API key" in r.json()["detail"]

    def test_test_connection_nvidia_without_config(self, client):
        """NVIDIA with empty base_url/model → config save fails with 400,
        and the test-connection endpoint reports mock (default) is ok."""
        # Attempt to save nvidia config with empty base_url — should 400
        r = client.put("/api/v1/provider/config", json={
            "provider": "nvidia",
            "base_url": "",
            "model": "",
        })
        assert r.status_code == 400
        # Config was NOT saved — still mock
        r2 = client.get("/api/v1/provider/config")
        assert r2.json()["provider"] == "mock"
        # Set a key (so it exists in store)
        client.put("/api/v1/provider/api-key", json={"api_key": "nvapi-test"})
        # Test connection — mock is always ok
        r3 = client.post("/api/v1/provider/test")
        assert r3.status_code == 200
        assert r3.json()["ok"] is True

    def test_reset(self, client):
        # Save some config
        client.put("/api/v1/provider/config", json={
            "provider": "nvidia",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model": "test-model",
        })
        client.put("/api/v1/provider/api-key", json={"api_key": "nvapi-test"})

        # Reset
        r = client.post("/api/v1/provider/reset")
        assert r.status_code == 200

        # Verify reset
        r2 = client.get("/api/v1/provider/config")
        data = r2.json()
        assert data["provider"] == "mock"
        assert data["api_key_set"] is False

    def test_get_defaults(self, client):
        r = client.get("/api/v1/provider/defaults")
        assert r.status_code == 200
        data = r.json()
        assert "mock" in data
        assert "nvidia" in data
        assert data["nvidia"]["model"] == "z-ai/glm-5.3"
        assert data["nvidia"]["base_url"] == "https://integrate.api.nvidia.com/v1"

    def test_no_secrets_in_responses(self, client):
        """Verify the API key never appears in any response."""
        client.put("/api/v1/provider/api-key", json={
            "api_key": "nvapi-SUPERSECRET1234567890"
        })

        # Check all read endpoints
        for endpoint in ["/api/v1/provider/config", "/api/v1/provider/defaults"]:
            r = client.get(endpoint)
            assert r.status_code == 200
            body = r.text
            assert "SUPERSECRET" not in body
            assert "nvapi-SUPER" not in body


__all__ = ["TestSettingsStore", "TestProviderSettingsEndpoints"]
