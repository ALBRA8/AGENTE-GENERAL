"""Smoke tests for the Render ASGI entry point and public probes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from render_app import app


def test_render_entrypoint_exposes_fastapi_application() -> None:
    """The configured Render module must expose a usable FastAPI app."""
    assert app.title == "albra-seed-api"
    paths = {route.path for route in app.routes}
    assert "/" in paths
    assert "/api/v1/health" in paths
    assert "/api/v1/readiness" in paths


def test_root_endpoint_returns_service_metadata() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "albra-seed-api"
    assert payload["public_prefix"] == "/api/v1"


def test_health_endpoint_responds_without_external_provider_credentials() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert "status" in payload
    assert "phase" in payload
    assert "core_only" in payload


def test_readiness_endpoint_returns_boolean_probe() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/readiness")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["ready"], bool)
    assert isinstance(payload["phase"], str)
