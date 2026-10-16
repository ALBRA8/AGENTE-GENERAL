"""Smoke tests for the supported local ASGI entrypoint."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # type: ignore[import-untyped]

from albra_seed.local_api import app, runtime
from albra_seed.runtime.lifecycle import LifecyclePhase


def test_local_api_lifespan_and_health_endpoints() -> None:
    """The local entrypoint starts the runtime and serves health/readiness."""
    with TestClient(app) as client:
        assert runtime.lifecycle.phase == LifecyclePhase.READY

        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert isinstance(health.json(), dict)

        readiness = client.get("/api/v1/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["ready"] is True

            monkeypatch.delenv("ALBRA_PROVIDER_SETTINGS_TOKEN", raising=False)
            response = client.get("/api/v1/provider/config")
            assert response.status_code == 503

            monkeypatch.setenv("ALBRA_PROVIDER_SETTINGS_TOKEN", "short")
            response = client.get("/api/v1/provider/config")
            assert response.status_code == 503

            token = "test-only-provider-settings-token-32chars"
            monkeypatch.setenv("ALBRA_PROVIDER_SETTINGS_TOKEN", token)

            unauthorized = client.get(
                "/api/v1/provider/config",
                headers={"Authorization": "Bearer wrong-token"},
            )
            assert unauthorized.status_code == 401

            authorized = client.get(
                "/api/v1/provider/config",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert authorized.status_code == 200
            payload = authorized.json()
            assert "api_key" not in payload
            assert "api_key_set" in payload

            invalid_key = "TEST-SECRET-MUST-NOT-LEAK-" + ("x" * 4100)
            invalid_response = client.put(
                "/api/v1/provider/api-key",
                headers={"Authorization": f"Bearer {token}"},
                json={"api_key": invalid_key},
            )
            assert invalid_response.status_code == 422
            assert "TEST-SECRET-MUST-NOT-LEAK" not in invalid_response.text

    assert runtime.lifecycle.phase == LifecyclePhase.STOPPED

