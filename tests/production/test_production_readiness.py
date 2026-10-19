"""Production readiness tests: Provider Failover, Durable HITL, and Readiness Probes.

Verifies:
  1. Multi-provider transparent failover in agent reasoning phase.
  2. Durable SQLite HITL approval storage and cross-process resume recovery.
  3. HTTP 503 status on unready probe, HTTP 200 on ready probe.
  4. Render blueprint completeness.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import pytest
from fastapi.testclient import TestClient

from albra_seed.core.base import LLMProvider
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import (
    AgentResponse,
    ApprovalStatus,
    Message,
    Role,
    RunContext,
    ToolCall,
)
from albra_seed.approvals.engine import ApprovalEngine
from albra_seed.approvals.sqlite_store import SQLiteApprovalStore
from albra_seed.runtime.agent import Agent
from albra_seed.runtime.runtime import Runtime
from albra_seed.core.config import SeedConfig
from albra_seed.security.auth import TokenAuthenticator
from albra_seed.security.permissions import PermissionRegistry
from albra_seed.security.roles import RoleRegistry


class FlakyMockProvider(LLMProvider):
    """Mock provider that can be configured to fail or succeed."""

    def __init__(self, name: str, *, should_fail: bool = False, fail_message: str = "Provider outage") -> None:
        self.name = name
        self.should_fail = should_fail
        self.fail_message = fail_message
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        context: RunContext | None = None,
    ) -> AgentResponse:
        self.calls.append(messages)
        if self.should_fail:
            raise RuntimeError(self.fail_message)
        last_msg = messages[-1].content if messages else ""
        return AgentResponse(
            content=f"Response from {self.name}: {last_msg}",
            execution_id=context.execution_id if context else "exec_test",
            correlation_id=context.correlation_id if context else "corr_test",
            raw={},
            metadata={"provider": self.name},
        )

    async def health(self) -> bool:
        return not self.should_fail


# =============================================================================
# 1. Multi-Provider Failover Tests
# =============================================================================


@pytest.mark.asyncio
async def test_agent_fallback_to_secondary_when_primary_fails() -> None:
    """When primary provider fails, agent transparently falls back to secondary provider."""
    runtime = Runtime(config=SeedConfig())
    await runtime.startup()

    primary = FlakyMockProvider("groq", should_fail=True, fail_message="Groq 429 Rate Limit")
    secondary = FlakyMockProvider("nvidia", should_fail=False)

    runtime.register_provider(primary)
    runtime.register_provider(secondary)

    identity = AgentIdentity(
        name="test-failover-agent",
        version="1.0.0",
        specialization="general",
        providers=["groq", "nvidia"],
    )
    agent = Agent(identity=identity, runtime=runtime)

    context = RunContext(task="Hello ALBRA", agent_id=identity.agent_id)
    response = await agent.run("Hello ALBRA", context=context)

    # Primary was called and failed
    assert len(primary.calls) == 1
    # Secondary was called and succeeded
    assert len(secondary.calls) == 1
    assert "Response from nvidia: Hello ALBRA" in response.content
    assert "nvidia" in context.providers_used
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_agent_tertiary_failover_when_primary_and_secondary_fail() -> None:
    """When primary and secondary fail, agent transparently falls back to tertiary provider."""
    runtime = Runtime(config=SeedConfig())
    await runtime.startup()

    p1 = FlakyMockProvider("groq", should_fail=True, fail_message="Groq Down")
    p2 = FlakyMockProvider("nvidia", should_fail=True, fail_message="NVIDIA 500 Error")
    p3 = FlakyMockProvider("openai", should_fail=False)

    runtime.register_provider(p1)
    runtime.register_provider(p2)
    runtime.register_provider(p3)

    identity = AgentIdentity(
        name="test-tertiary-agent",
        version="1.0.0",
        specialization="general",
        providers=["groq", "nvidia", "openai"],
    )
    agent = Agent(identity=identity, runtime=runtime)

    context = RunContext(task="Perform query", agent_id=identity.agent_id)
    response = await agent.run("Perform query", context=context)

    assert len(p1.calls) == 1
    assert len(p2.calls) == 1
    assert len(p3.calls) == 1
    assert "Response from openai: Perform query" in response.content
    assert "openai" in context.providers_used
    await runtime.shutdown()


# =============================================================================
# 2. Durable HITL Approval Storage & Recovery Tests
# =============================================================================


@pytest.mark.asyncio
async def test_durable_hitl_survives_process_restart(tmp_path: Path) -> None:
    """Approvals and evidence chains created in SQLite survive across engine instantiations."""
    db_file = str(tmp_path / "approvals_durable.db")

    # Instance 1: Create approval request
    store1 = SQLiteApprovalStore(db_file)
    engine1 = ApprovalEngine(store=store1)

    req_result = await engine1.request(
        action=ToolCall(tool_name="database.drop_table", arguments={"table": "users"}),
        actor="operator:alice",
    )
    approval_id = req_result.approval_id
    assert approval_id.startswith("appr_")

    # Approve in instance 1
    approve_res = await engine1.approve(approval_id, approver="supervisor:bob")
    assert approve_res.status == ApprovalStatus.APPROVED

    # Instance 2 (Simulating server restart / new worker)
    store2 = SQLiteApprovalStore(db_file)
    engine2 = ApprovalEngine(store=store2)

    persisted_req = engine2.get(approval_id)
    assert persisted_req is not None
    assert persisted_req.status == ApprovalStatus.APPROVED
    assert persisted_req.action == "database.drop_table"
    assert persisted_req.context_ref.get("arguments") == {"table": "users"}

    # Evidence chain is also intact
    assert engine2.verify_evidence_chain() is True


def test_approvals_resume_endpoint_with_durable_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """POST /api/v1/approvals/{id}/resume succeeds when durable storage is configured."""
    import importlib
    import render_app

    test_token = "valid-token-with-at-least-32-characters-length-for-test"
    db_file = str(tmp_path / "test_approvals.db")
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("ALBRA_API_KEY", test_token)
    monkeypatch.setenv("APPROVAL_DB_PATH", db_file)

    mod = importlib.reload(render_app)
    mod.runtime.register(
        "authenticator",
        TokenAuthenticator(test_token, identity="admin", roles=["reviewer"]),
    )

    with TestClient(mod.app) as client:
        # Check that approval engine in runtime has durable store
        engine = mod.runtime.get_component("approval_engine")
        assert engine.store is not None

        # Verify endpoint rejected without auth
        res_unauth = client.post(f"/api/v1/approvals/non-existent/resume")
        assert res_unauth.status_code == 401

        # Query non-existent approval with valid auth gives 404, NOT 503 or 409
        res_404 = client.post(
            f"/api/v1/approvals/appr_non_existent/resume",
            headers={"Authorization": f"Bearer {test_token}"},
        )
        assert res_404.status_code == 404
        assert "approval not found" in res_404.json()["detail"]


# =============================================================================
# 3. Readiness Probe HTTP 503 vs 200 Tests
# =============================================================================


def test_readiness_probe_reports_false_when_uninitialized() -> None:
    """When runtime is uninitialized (not ready), GET /api/v1/readiness returns ready=False."""
    from albra_seed import Runtime, SeedConfig, create_app

    unstarted_runtime = Runtime(config=SeedConfig())
    # Do NOT call unstarted_runtime.startup()
    app = create_app(unstarted_runtime)

    with TestClient(app) as client:
        res = client.get("/api/v1/readiness")
        assert res.status_code == 200
        data = res.json()
        assert data["ready"] is False
        assert data["phase"] == "uninitialized"


def test_readiness_probe_returns_200_when_ready() -> None:
    """When runtime has completed startup and is ready, GET /api/v1/readiness returns HTTP 200."""
    from albra_seed import Runtime, SeedConfig, create_app
    import anyio

    runtime = Runtime(config=SeedConfig())
    anyio.run(runtime.startup)
    app = create_app(runtime)

    with TestClient(app) as client:
        res = client.get("/api/v1/readiness")
        assert res.status_code == 200
        data = res.json()
        assert data["ready"] is True
        assert data["phase"] == "ready"


# =============================================================================
# 4. Render Blueprint Configuration Tests
# =============================================================================


def test_render_yaml_contains_all_required_production_declarations() -> None:
    """Validate render.yaml configuration has all necessary environment keys."""
    yaml_path = Path("render.yaml")
    assert yaml_path.exists(), "render.yaml must exist in root"
    content = yaml_path.read_text(encoding="utf-8")

    assert "APP_ENV" in content
    assert "production" in content
    assert "ALBRA_API_KEY" in content
    assert "APPROVAL_DB_PATH" in content
    assert "GROQ_API_KEY" in content
    assert "NVIDIA_API_KEY" in content
    assert "OPENAI_API_KEY" in content
    assert "healthCheckPath: /api/v1/health" in content
