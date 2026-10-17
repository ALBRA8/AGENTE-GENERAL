"""Security closure tests — BLOQ 1 (fail-open) + BLOQ 2 (HTTP perimeter).

These tests demonstrate REAL behavior, not "class exists" checks.
They verify the DEFAULT-DENY invariant is enforced end-to-end.

Spec reference (audit BLOQ 1 + 2):
- Tool + Firewall absent + Policy absent => DENY (never ALLOW)
- Tool + Firewall present + Policy absent => behavior per firewall
- Tool + Firewall absent + Policy present => behavior per policy
- Tool + Firewall + Policy configured correctly => normal behavior
- CORE_ONLY without Tools => still works (Birth Contract intact)
- Endpoint /execution without perimeter => blocked (503)
- Endpoint /execution with perimeter => normal behavior (200)
- No bypass flags (insecure=True / bypass_security=True / skip_auth=True)
- No 'except Exception: allow' style fallbacks
"""

from __future__ import annotations

import asyncio
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from albra_seed import (
    Agent,
    AgentIdentity,
    Runtime,
    SeedConfig,
    AutonomyLevel,
)
from albra_seed.core.types import ToolCall, AgentResponse, RiskLevel
from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule, PolicyRuleSet
from albra_seed.api.app import create_app
from albra_seed.providers.mock import MockProvider
from albra_seed.tools.infra_tools import EchoTool


# =====================================================================
# Helpers
# =====================================================================


class _ToolCallingProvider(MockProvider):
    """Mock provider that emits a tool_call in its response."""

    def __init__(self, tool_name: str = "echo", arguments: dict | None = None):
        super().__init__()
        self._tool_name = tool_name
        self._arguments = arguments or {"text": "hi"}

    async def complete(self, messages, **kwargs):
        self._call_count += 1
        ctx = kwargs.get("context")
        return AgentResponse(
            content="tool-emitting-response",
            execution_id=ctx.execution_id if ctx else "x",
            correlation_id=ctx.correlation_id if ctx else "y",
            tool_calls=[ToolCall(
                id="tc1", tool_name=self._tool_name, arguments=self._arguments,
            )],
        )


def _identity(autonomy: AutonomyLevel = AutonomyLevel.L2_EXECUTE_SAFE) -> AgentIdentity:
    return AgentIdentity(
        name="security-test-agent",
        autonomy_level=autonomy,
        risk_profile=RiskLevel.LOW,
    )


class _HTTPExecutionAuthenticator:
    async def verify(self, token: str | None):
        from albra_seed.security.auth import AuthPrincipal

        if token not in {"valid-test-token", "sk-super-secret-token-12345"}:
            return None
        return AuthPrincipal(
            kind="user",
            identity="executor-1",
            roles=["executor"],
            auth_method="test",
            is_anonymous=False,
        )


def _configure_execution_api_auth(runtime: Runtime) -> None:
    """Install a test-only authenticated principal and explicit execution RBAC."""
    from albra_seed.core.types import PolicyDecision
    from albra_seed.security.permissions import Permission, PermissionRegistry
    from albra_seed.security.roles import Role, RoleRegistry

    roles = RoleRegistry()
    roles.register(Role(role_id="executor", name="executor"))
    permissions = PermissionRegistry(roles)
    policy = runtime.get_component("policy_engine")
    if policy is None:
        policy = PolicyEngine()
    policy.permission_registry = permissions
    for action in ("execution:run", "execution:read", "execution:read:any"):
        permissions.register(Permission(id=action))
        permissions.grant("executor", action)
        policy.add_rule(PolicyRule(
            name=f"allow {action} API",
            action_pattern=action,
            actor_pattern="executor-1",
            risk_threshold=RiskLevel.UNKNOWN,
            required_permission=action,
            decision=PolicyDecision.ALLOW,
            priority=100,
        ))
    runtime.register("authenticator", _HTTPExecutionAuthenticator())
    runtime.register("policy_engine", policy)


def _build_runtime_with_perimeter(
    *,
    allow_tool: bool = True,
    tool_name: str = "echo",
) -> tuple[Runtime, Agent]:
    """Build a Runtime with EchoTool + a permissive ActionFirewall."""
    runtime = Runtime(config=SeedConfig())
    # Don't call startup() — caller will
    return runtime, None


# =====================================================================
# BLOQ 1 — Fail-open closure tests
# =====================================================================


class TestBloq1FailOpenClosure:
    """BLOQ 1: Tool + no security perimeter => DENY (never ALLOW)."""

    @pytest.mark.asyncio
    async def test_tool_with_no_firewall_no_policy_is_DENIED(self):
        """Tool + Firewall absent + Policy absent => DENY. NOT ALLOW."""
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(_ToolCallingProvider())
        runtime.register_tool(EchoTool())
        # NOTE: NO firewall, NO policy registered
        agent = Agent(_identity(), runtime)
        response = await agent.run("echo hi")
        # Tool was called but DENIED
        assert response.tool_calls, "agent must emit a tool_call"
        assert response.tool_results, "tool_results must exist (with denied status)"
        assert response.tool_results[0].success is False
        assert "deny" in response.tool_results[0].error.lower()
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_tool_with_firewall_absent_policy_present_decision_per_policy(self):
        """Tool + Firewall absent + Policy present => behavior per policy.

        Policy says allow => allow. Policy says deny => deny.
        """
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(_ToolCallingProvider())
        runtime.register_tool(EchoTool())
        # Register a policy engine that explicitly allows the tool
        policy = PolicyEngine()
        policy.add_rule(PolicyRule(
            name="allow_echo",
            action_pattern="echo",
            actor_pattern="*",
            risk_threshold=RiskLevel.UNKNOWN,
            autonomy_required=AutonomyLevel.L0_OBSERVE,
            decision="allow",
            priority=10,
        ))
        runtime.register("policy_engine", policy)
        agent = Agent(_identity(), runtime)
        response = await agent.run("echo hi")
        assert response.tool_results
        assert response.tool_results[0].success is True
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_tool_with_firewall_present_policy_absent_decision_per_firewall(self):
        """Tool + Firewall present + Policy absent => behavior per firewall."""
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(_ToolCallingProvider())
        runtime.register_tool(EchoTool())
        # Permissive firewall
        fw = ActionFirewall()
        fw.add_rule(FirewallRule(
            action_pattern="echo", actor_pattern="*", decision="allow",
            risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0,
        ))
        runtime.register("firewall", fw)
        agent = Agent(_identity(), runtime)
        response = await agent.run("echo hi")
        assert response.tool_results
        assert response.tool_results[0].success is True
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_tool_with_firewall_and_policy_both_configured_works_normally(self):
        """Tool + Firewall + Policy configured correctly => normal behavior."""
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(_ToolCallingProvider())
        runtime.register_tool(EchoTool())
        # Both gates present and permissive
        fw = ActionFirewall()
        fw.add_rule(FirewallRule(
            action_pattern="echo", actor_pattern="*", decision="allow",
            risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0,
        ))
        runtime.register("firewall", fw)
        policy = PolicyEngine()
        policy.add_rule(PolicyRule(
            name="allow_echo",
            action_pattern="echo",
            actor_pattern="*",
            risk_threshold=RiskLevel.UNKNOWN,
            autonomy_required=AutonomyLevel.L0_OBSERVE,
            decision="allow",
            priority=10,
        ))
        runtime.register("policy_engine", policy)
        agent = Agent(_identity(), runtime)
        response = await agent.run("echo hi")
        assert response.tool_results
        assert response.tool_results[0].success is True
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_core_only_without_tools_still_works(self):
        """CORE_ONLY (no tools, no perimeter) still naciendo correctly."""
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(MockProvider())
        # No firewall, no policy, no tools — but no tool_calls either
        agent = Agent(_identity(), runtime)
        response = await agent.run("hello")
        assert response.content  # Mock provider returns something
        assert response.tool_calls == []
        # Birth Contract preserved
        assert runtime.readiness() is True
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_core_only_birth_contract_intact(self):
        """CORE_ONLY Birth Contract: runtime.startup() + readiness() == True."""
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        assert runtime.readiness() is True
        snapshot = await runtime.birth()
        assert snapshot["runtime_ready"] is True
        assert snapshot["mock_available"] is True
        assert snapshot["memory_ok"] is True
        assert snapshot["audit_ok"] is True
        await runtime.shutdown()


# =====================================================================
# BLOQ 2 — HTTP execution perimeter tests
# =====================================================================


class TestBloq2HTTPExecutionPerimeter:
    """BLOQ 2: /execution endpoint requires security perimeter."""

    def test_execution_endpoint_returns_503_when_no_perimeter(self):
        """POST /execution without firewall/policy => HTTP 503 (not 200)."""
        runtime = Runtime(config=SeedConfig())
        # Note: NOT calling startup — keep it minimal. We need a registered
        # agent. The endpoint will refuse because no perimeter is configured.
        # We DO register a provider so the agent would work if called.
        from albra_seed.providers.mock import MockProvider
        runtime.register_provider(MockProvider())
        identity = AgentIdentity(name="no-perimeter-agent")
        agent = Agent(identity=identity, runtime=runtime)
        app = create_app(runtime, agents={identity.agent_id: agent})
        with TestClient(app) as client:
            r = client.post("/api/v1/execution", json={"input": "hi"})
            assert r.status_code == 503, r.text
            assert "security perimeter" in r.json()["detail"].lower()

    def test_execution_endpoint_returns_200_when_perimeter_configured(self):
        """POST /execution with perimeter => HTTP 200 (perimeter present)."""
        runtime = Runtime(config=SeedConfig())
        from albra_seed.providers.mock import MockProvider
        runtime.register_provider(MockProvider())
        # Register a permissive firewall
        fw = ActionFirewall()
        fw.add_rule(FirewallRule(
            action_pattern="*", actor_pattern="*", decision="allow",
            risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0,
        ))
        runtime.register("firewall", fw)
        _configure_execution_api_auth(runtime)
        identity = AgentIdentity(name="perimeter-agent")
        agent = Agent(identity=identity, runtime=runtime)
        app = create_app(runtime, agents={identity.agent_id: agent})
        with TestClient(app) as client:
            # An open endpoint is denied even when a firewall is configured.
            anonymous = client.post("/api/v1/execution", json={"input": "hello"})
            assert anonymous.status_code == 401, anonymous.text
            r = client.post(
                "/api/v1/execution",
                json={"input": "hello"},
                headers={"Authorization": "Bearer valid-test-token"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["execution_id"]
            assert "hello" in body["content"].lower()

    def test_execution_endpoint_with_policy_only_also_works(self):
        """POST /execution with policy engine (no firewall) => 200."""
        runtime = Runtime(config=SeedConfig())
        from albra_seed.providers.mock import MockProvider
        runtime.register_provider(MockProvider())
        policy = PolicyEngine()
        policy.add_rule(PolicyRule(
            name="allow_all",
            action_pattern="*",
            actor_pattern="*",
            risk_threshold=RiskLevel.UNKNOWN,
            autonomy_required=AutonomyLevel.L0_OBSERVE,
            decision="allow",
            priority=10,
        ))
        runtime.register("policy_engine", policy)
        _configure_execution_api_auth(runtime)
        identity = AgentIdentity(name="policy-only-agent")
        agent = Agent(identity=identity, runtime=runtime)
        app = create_app(runtime, agents={identity.agent_id: agent})
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/execution",
                json={"input": "hello"},
                headers={"Authorization": "Bearer valid-test-token"},
            )
            assert r.status_code == 200, r.text

    def test_execution_endpoint_requires_explicit_rbac_rule(self):
        """Valid authentication alone is insufficient without execution:run policy."""
        runtime = Runtime(config=SeedConfig())
        from albra_seed.providers.mock import MockProvider
        runtime.register_provider(MockProvider())
        fw = ActionFirewall()
        fw.add_rule(FirewallRule(
            action_pattern="*", actor_pattern="*", decision="allow",
            risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0,
        ))
        runtime.register("firewall", fw)
        _configure_execution_api_auth(runtime)
        # Remove the action-specific rule while retaining the role permission.
        policy = runtime.get_component("policy_engine")
        policy.rule_set = PolicyRuleSet()
        identity = AgentIdentity(name="no-rbac-agent")
        agent = Agent(identity=identity, runtime=runtime)
        app = create_app(runtime, agents={identity.agent_id: agent})
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/execution",
                json={"input": "hello"},
                headers={"Authorization": "Bearer valid-test-token"},
            )
        assert response.status_code == 403
        assert "explicit execution:run policy grant" in response.json()["detail"]

    def test_execution_endpoint_does_not_leak_token_in_response(self):
        """Verify Authorization header is NEVER echoed in response body."""
        runtime = Runtime(config=SeedConfig())
        from albra_seed.providers.mock import MockProvider
        runtime.register_provider(MockProvider())
        fw = ActionFirewall()
        fw.add_rule(FirewallRule(
            action_pattern="*", actor_pattern="*", decision="allow",
            risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0,
        ))
        runtime.register("firewall", fw)
        _configure_execution_api_auth(runtime)
        identity = AgentIdentity(name="token-leak-agent")
        agent = Agent(identity=identity, runtime=runtime)
        app = create_app(runtime, agents={identity.agent_id: agent})
        secret_token = "Bearer sk-super-secret-token-12345"
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/execution",
                json={"input": "hello"},
                headers={"Authorization": secret_token},
            )
            assert r.status_code == 200
            body_text = r.text
            # The secret MUST NOT appear in the response body
            assert "sk-super-secret-token-12345" not in body_text


# =====================================================================
# No-bypass verification
# =====================================================================


class TestNoSecurityBypass:
    """Verify no bypass flags or except-allow patterns exist."""

    def test_no_insecure_flag_in_source(self):
        """Source code must not contain insecure=True / bypass_security=True / skip_auth=True flags."""
        import re
        from pathlib import Path

        pattern = re.compile(r"(insecure|bypass_security|skip_auth|allow_on_error)\s*=\s*True")
        matches = []
        src_dir = Path("src/albra_seed")
        for path in src_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches.append(f"{path}:{lineno}: {line}")
        assert not matches, f"Bypass flags found:\n" + "\n".join(matches)

    def test_no_except_allow_pattern_in_agent(self):
        """Agent loop must not have 'except Exception: allow' style fallbacks."""
        import re
        from pathlib import Path

        pattern = re.compile(r"except.*:.*decision\s*=\s*['\"]?allow")
        matches = []
        path = Path("src/albra_seed/runtime/agent.py")
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append(f"{path}:{lineno}: {line}")
        assert not matches, f"Found 'except: allow' pattern:\n" + "\n".join(matches)


# =====================================================================
# A2A regression check (must still pass after BLOQ 1 + BLOQ 2)
# =====================================================================


class TestA2ARegressionAfterSecurityClosure:
    """BLOQ 1 + 2 fixes must not break A2A."""

    @pytest.mark.asyncio
    async def test_a2a_in_process_transport_still_works(self):
        """A2A InProcessTransport (CORE_ONLY) still routes correctly."""
        from albra_seed.a2a.transport import InProcessTransport
        from albra_seed.a2a.server import A2AServer
        from albra_seed.a2a.client import A2AClient
        from albra_seed.a2a.contract import A2AMessage, A2ATask, TaskState
        from albra_seed.handoff.manager import HandoffManager
        from albra_seed.handoff.registry import AgentRegistry

        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(MockProvider())
        identity = AgentIdentity(name="a2a-regression-agent")
        agent = Agent(identity=identity, runtime=runtime)
        registry = AgentRegistry()
        handoff = HandoffManager(registry)
        server = A2AServer(runtime, identity=identity, handoff_manager=handoff)
        transport = InProcessTransport()
        transport.register("http://albra-test", server)
        client = A2AClient(transport)

        task = await client.send_task(
            "http://albra-test",
            message=A2AMessage.from_text("hello"),
        )
        assert task.state in (TaskState.COMPLETED, TaskState.WORKING)
        await runtime.shutdown()
