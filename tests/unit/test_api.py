"""Unit tests for the api subpackage (F2-I)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # type: ignore[import-untyped]

from albra_seed.api.app import create_app
from albra_seed.core.config import SeedConfig
from albra_seed.core.identity import AgentIdentity
from albra_seed.runtime.agent import Agent
from albra_seed.runtime.runtime import Runtime


# =====================================================================
# Fixtures
# =====================================================================


@pytest.fixture
def runtime() -> Runtime:
    """A Runtime with a fresh config (no env vars leaking in)."""
    cfg = SeedConfig()
    rt = Runtime(config=cfg)
    return rt


@pytest.fixture
def app(runtime: Runtime) -> Any:
    return create_app(runtime)


@pytest.fixture
def client(app: Any) -> TestClient:
    with TestClient(app) as c:
        yield c


# =====================================================================
# create_app returns a FastAPI instance
# =====================================================================


def test_create_app_returns_fastapi(runtime: Runtime) -> None:
    """create_app() returns a fastapi.FastAPI instance wired to the runtime."""
    from fastapi import FastAPI  # type: ignore[import-untyped]
    app = create_app(runtime)
    assert isinstance(app, FastAPI)
    # The app exposes a root index, /api/v1/health, /api/v1/readiness,
    # /api/v1/agent/info, /api/v1/agent/capabilities, /api/v1/execution,
    # /internal/diagnostics.
    # Walk app.routes recursively — included routers (starlette._IncludedRouter)
    # don't expose .path directly; flatten their inner .routes.
    def _all_paths(routes):
        paths = set()
        for r in routes:
            if hasattr(r, "path"):
                paths.add(r.path)
            if hasattr(r, "routes"):
                paths |= _all_paths(r.routes)
        return paths
    routes = _all_paths(app.routes)
    assert "/api/v1/health" in routes
    assert "/api/v1/readiness" in routes
    assert "/api/v1/agent/info" in routes
    assert "/api/v1/agent/capabilities" in routes
    assert "/api/v1/execution" in routes
    assert "/internal/diagnostics" in routes


# =====================================================================
# Health endpoint returns a dict
# =====================================================================


def test_health_endpoint_returns_dict(client: TestClient) -> None:
    """GET /api/v1/health returns the runtime's health snapshot."""
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert "status" in body
    assert "phase" in body
    assert "core_only" in body
    assert "components" in body
    assert "sessions" in body
    assert "uptime_seconds" in body


# =====================================================================
# Readiness endpoint returns a bool
# =====================================================================


def test_readiness_endpoint_returns_bool(client: TestClient) -> None:
    """GET /api/v1/readiness returns a `ready` boolean."""
    r = client.get("/api/v1/readiness")
    assert r.status_code == 200
    body = r.json()
    assert "ready" in body
    assert isinstance(body["ready"], bool)
    assert isinstance(body["phase"], str)


# =====================================================================
# Agent info endpoint returns the manifest
# =====================================================================


def test_agent_info_endpoint_returns_manifest(runtime: Runtime) -> None:
    """GET /api/v1/agent/info returns the agent manifest when an agent is
    registered; otherwise returns a minimal seed identity."""
    # Without an agent registered.
    app = create_app(runtime)
    with TestClient(app) as client:
        r = client.get("/api/v1/agent/info")
        assert r.status_code == 200
        body = r.json()
        assert body["agent_id"] == "seed"
        assert body["name"] == "albra-seed"
        assert body["seed_version"] == runtime.config.seed_version

    # With an agent registered.
    identity = AgentIdentity(name="test-agent")
    agent = Agent(identity=identity, runtime=runtime)
    app = create_app(runtime, agents={identity.agent_id: agent})
    with TestClient(app) as client:
        r = client.get("/api/v1/agent/info")
        assert r.status_code == 200
        body = r.json()
        assert body["agent_id"] == identity.agent_id
        assert body["name"] == "test-agent"
        assert body["version"] == identity.version


# =====================================================================
# Secrets are redacted in the diagnostics endpoint output
# =====================================================================


def test_secrets_are_redacted_in_config_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The diagnostics endpoint never leaks secrets; execution metadata is
    also redacted before returning."""
    # Set a real-looking API key in env.
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
              "ZAI_API_KEY", "ALBRA_VAULT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-attempt-12345678901234567890")

    rt = Runtime()
    app = create_app(rt)
    with TestClient(app) as client:
        # The diagnostics endpoint runs the Doctor and returns its report;
        # it should never echo secret values.
        r = client.get("/internal/diagnostics")
        assert r.status_code == 200
        text = r.text
        assert "sk-leak-attempt" not in text


# =====================================================================
# Execution endpoint round-trip (uses Mock provider fallback)
# =====================================================================


def test_execution_endpoint_runs_agent_and_returns_trace(runtime: Runtime) -> None:
    """POST /api/v1/execution runs the agent; GET /api/v1/execution/{id}
    returns the cached trace.

    Since BLOQ 2 (HTTP execution perimeter closure), the endpoint requires
    a security perimeter (firewall OR policy engine) on the Runtime. This
    test registers a permissive ActionFirewall so the perimeter is present.
    """
    from albra_seed.providers.mock import MockProvider
    from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
    from albra_seed.core.types import RiskLevel
    runtime.register_provider(MockProvider())
    # Security perimeter: register a permissive firewall (allows anything
    # that the agent's Mock provider emits — which is no tool calls).
    # The perimeter itself is what matters; default-deny is still enforced
    # by the runtime if a tool is actually attempted.
    fw = ActionFirewall()
    fw.add_rule(FirewallRule(
        action_pattern="*", actor_pattern="*", decision="allow",
        risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0,
    ))
    runtime.register("firewall", fw)

    identity = AgentIdentity(name="exec-agent", providers=["mock"])
    agent = Agent(identity=identity, runtime=runtime)
    app = create_app(runtime, agents={identity.agent_id: agent})

    with TestClient(app) as client:
        r = client.post(
            "/api/v1/execution",
            json={"input": "hello"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["execution_id"]
        assert body["correlation_id"]
        # Mock provider echoes the input back.
        assert "hello" in body["content"].lower()

        # Trace lookup
        r2 = client.get(f"/api/v1/execution/{body['execution_id']}")
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["execution_id"] == body["execution_id"]
        assert body2["found"] is True
        assert isinstance(body2["trace"], list)



def test_execution_endpoint_forwards_bearer_token(runtime: Runtime) -> None:
    """Bearer credentials reach Agent.run for Runtime authenticator verification."""
    from albra_seed.providers.mock import MockProvider
    from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
    from albra_seed.core.types import RiskLevel

    runtime.register_provider(MockProvider())
    fw = ActionFirewall()
    fw.add_rule(FirewallRule(
        action_pattern="*", actor_pattern="*", decision="allow",
        risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0,
    ))
    runtime.register("firewall", fw)
    from albra_seed.security.auth import AuthPrincipal, Authenticator

    class AcceptingAuthenticator(Authenticator):
        name = "accepting-test"

        async def authenticate(self, credentials: Any) -> AuthPrincipal:
            return AuthPrincipal(
                kind="user", identity="test-user", auth_method=self.name,
                is_anonymous=False,
            )

        async def verify(self, token: str | None) -> AuthPrincipal | None:
            if token != "test-credential":
                return None
            return AuthPrincipal(
                kind="user", identity="test-user", auth_method=self.name,
                is_anonymous=False,
            )

    runtime.register("authenticator", AcceptingAuthenticator())
    identity = AgentIdentity(name="token-forward-agent", providers=["mock"])
    agent = Agent(identity=identity, runtime=runtime)
    original_run = agent.run
    observed: dict[str, Any] = {}

    async def capture_token(*args: Any, **kwargs: Any) -> Any:
        observed["token"] = kwargs.get("token")
        return await original_run(*args, **kwargs)

    agent.run = capture_token  # type: ignore[method-assign]
    app = create_app(runtime, agents={identity.agent_id: agent})
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/execution",
            headers={"Authorization": "Bearer test-credential"},
            json={"input": "hello"},
        )
    assert response.status_code == 200, response.text
    assert observed["token"] == "test-credential"


def test_execution_endpoint_rejects_malformed_authorization_header(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/execution",
        headers={"Authorization": "Basic not-a-bearer"},
        json={"input": "hello"},
    )
    assert response.status_code == 401


# =====================================================================
# Internal diagnostics endpoint returns a DiagnosticReport
# =====================================================================


def test_internal_diagnostics_endpoint_returns_report(client: TestClient) -> None:
    """GET /internal/diagnostics runs the Doctor and returns the report."""
    r = client.get("/internal/diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert "generated_at" in body
    assert "overall_status" in body
    assert "summary" in body
    assert "components" in body
    assert isinstance(body["components"], list)


# =====================================================================
# Root index
# =====================================================================


def test_root_endpoint_returns_metadata(client: TestClient) -> None:
    """GET / returns API metadata and the route prefixes."""
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "albra-seed-api"
    assert body["public_prefix"] == "/api/v1"
    assert body["internal_prefix"] == "/internal"


def test_execution_endpoint_passes_bearer_to_registered_authenticator(
    runtime: Runtime,
) -> None:
    """The forwarded token is consumed by the Runtime's Authenticator."""
    from albra_seed.providers.mock import MockProvider
    from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
    from albra_seed.core.types import RiskLevel
    from albra_seed.security.auth import AuthPrincipal, Authenticator

    class RecordingAuthenticator(Authenticator):
        name = "recording-test"

        def __init__(self) -> None:
            self.seen_tokens: list[str | None] = []

        async def authenticate(self, credentials: Any) -> AuthPrincipal:
            return AuthPrincipal(
                kind="user", identity="test-user", auth_method=self.name,
                is_anonymous=False,
            )

        async def verify(self, token: str | None) -> AuthPrincipal | None:
            self.seen_tokens.append(token)
            if token != "valid-test-token":
                return None
            return AuthPrincipal(
                kind="user", identity="test-user", auth_method=self.name,
                is_anonymous=False,
            )

    runtime.register_provider(MockProvider())
    fw = ActionFirewall()
    fw.add_rule(FirewallRule(
        action_pattern="*", actor_pattern="*", decision="allow",
        risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0,
    ))
    runtime.register("firewall", fw)
    authenticator = RecordingAuthenticator()
    runtime.register("authenticator", authenticator)
    identity = AgentIdentity(name="authenticator-integration-agent", providers=["mock"])
    agent = Agent(identity=identity, runtime=runtime)
    app = create_app(runtime, agents={identity.agent_id: agent})

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/execution",
            headers={"Authorization": "Bearer valid-test-token"},
            json={"input": "hello"},
        )

    assert response.status_code == 200, response.text
    assert "valid-test-token" in authenticator.seen_tokens

    with TestClient(app) as client:
        rejected = client.post(
            "/api/v1/execution",
            headers={"Authorization": "Bearer invalid-test-token"},
            json={"input": "should not run"},
        )
    assert rejected.status_code == 401
    assert "invalid-test-token" in authenticator.seen_tokens
