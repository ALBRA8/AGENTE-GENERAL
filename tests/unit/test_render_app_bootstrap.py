"""Regression tests for the Render ASGI entry point."""

from fastapi.testclient import TestClient

from render_app import app, identity


def test_render_entrypoint_registers_generic_agent() -> None:
    """The public agent manifest must exist before the app serves requests."""
    agent = app.state.ctx.get_agent(identity.agent_id)
    assert agent is not None
    assert agent.identity.name == "albra-seed"


def test_render_lifespan_initializes_runtime_and_readiness() -> None:
    """ASGI startup must initialize the runtime instead of leaving it degraded."""
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        readiness = client.get("/api/v1/readiness")
        capabilities = client.get("/api/v1/agent/capabilities")

    assert health.status_code == 200
    assert health.json()["phase"] == "ready"
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True
    assert capabilities.status_code == 200
    assert capabilities.json()["count"] == 1
    assert capabilities.json()["capabilities"][0]["name"] == "text-generation"
