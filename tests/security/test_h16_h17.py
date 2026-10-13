"""Regression tests for H-16 (issuer/verifier compatibility) and H-17 (context_id empty).

H-16: If an external auth_verifier is provided without a matching issuer,
the agent must fail-closed — no tokens can be issued.
If no external verifier is provided, the agent creates its own issuer
and derives a verifier from it — both share the same key.

H-17: context_id="" and context_id=None must be normalized identically.
A token issued with normalized context_id must be verifiable by the
executor using the same normalized value on RunContext.
"""
from __future__ import annotations

import pytest
from typing import Any

from albra_seed import (
    Agent, AgentIdentity, Runtime, SeedConfig,
    AutonomyLevel, RiskLevel, RunContext,
)
from albra_seed.core.types import ToolCall, AgentResponse
from albra_seed.core.base import ToolContract
from albra_seed.providers.mock import MockProvider
from albra_seed.tools.infra_tools import EchoTool
from albra_seed.tools.executor import ToolExecutor
from albra_seed.tools.registry import ToolRegistry
from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule
from albra_seed.security.auth import AuthPrincipal
from albra_seed.security.trusted_principal import (
    TrustedPrincipal, TrustedPrincipalIssuer, TrustedPrincipalVerifier,
)
from albra_seed.security.auth_token import (
    AuthorizationIssuer, AuthorizationVerifier,
)


class _SpyTool(EchoTool):
    def __init__(self, name="spy"):
        super().__init__()
        self.execute_count = 0
        self.contract = ToolContract(
            name=name, description="spy",
            input_schema={"message": {"type": "string"}}, output_schema={},
            risk_level=RiskLevel.LOW, permissions=[], timeout_seconds=30,
            retry_policy={"max": 0}, evidence_required=False,
            is_dry_run_capable=False, is_external=False, availability="available",
        )
    async def execute(self, arguments, *, context=None, dry_run=False):
        self.execute_count += 1
        return await super().execute(arguments, context=context, dry_run=dry_run)


class _ToolCallingProvider(MockProvider):
    def __init__(self, tool_calls):
        super().__init__()
        self._tool_calls = tool_calls
    async def complete(self, messages, **kwargs):
        self._call_count += 1
        ctx = kwargs.get("context")
        return AgentResponse(
            content="[mock]",
            execution_id=ctx.execution_id if ctx else "x",
            correlation_id=ctx.correlation_id if ctx else "y",
            tool_calls=list(self._tool_calls),
        )


def _build_runtime(*, allow_tools=None, auth_verifier=None):
    runtime = Runtime(config=SeedConfig())
    runtime.register_provider(_ToolCallingProvider([
        ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})
    ]))
    runtime.register_tool(EchoTool())
    fw = ActionFirewall()
    for t in (allow_tools or ["echo"]):
        fw.add_rule(FirewallRule(action_pattern=t, actor_pattern="*", decision="allow",
                                 risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0))
    runtime.register("firewall", fw)
    policy = PolicyEngine()
    for t in (allow_tools or ["echo"]):
        policy.add_rule(PolicyRule(name=f"allow_{t}", action_pattern=t, actor_pattern="*",
                                   risk_threshold=RiskLevel.UNKNOWN, autonomy_required=AutonomyLevel.L0_OBSERVE,
                                   decision="allow", priority=10))
    runtime.register("policy_engine", policy)
    return runtime


# =====================================================================
# H-16: Issuer / Verifier compatibility
# =====================================================================


class TestH16IssuerVerifierCompatibility:
    """Issuer and verifier must share the same cryptographic authority."""

    @pytest.mark.asyncio
    async def test_legitimate_execution_with_self_created_verifier(self):
        """No external verifier → agent creates issuer+verifier pair → works."""
        runtime = _build_runtime()
        await runtime.startup()
        spy = _SpyTool("echo")
        runtime.register_tool(spy)
        identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
        agent = Agent(identity, runtime)
        response = await agent.run("echo hi")
        assert spy.execute_count == 1
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_external_verifier_without_matching_issuer_fails_closed(self):
        """External verifier provided but no issuer → fail-closed, tool NOT executed."""
        runtime = _build_runtime()
        await runtime.startup()
        spy = _SpyTool("echo")
        runtime.register_tool(spy)
        # Create an EXTERNAL issuer + verifier (not shared with the agent)
        external_issuer = AuthorizationIssuer()
        external_verifier = AuthorizationVerifier(external_issuer)
        identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
        agent = Agent(identity, runtime, auth_verifier=external_verifier)
        response = await agent.run("echo hi")
        # Tool should NOT execute — the agent can't issue tokens that the
        # external verifier would accept (different signing key)
        assert spy.execute_count == 0, f"Tool executed {spy.execute_count} times — should be 0 (fail-closed)"
        # Response should have tool_results with failures
        assert response.tool_results
        assert not response.tool_results[0].success
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_token_signed_by_different_issuer_rejected(self):
        """Token signed by issuer A, verified by verifier B → rejected."""
        registry = ToolRegistry()
        spy = _SpyTool("echo")
        registry.register(spy)
        issuer_a = AuthorizationIssuer()
        issuer_b = AuthorizationIssuer()  # different key
        verifier_b = AuthorizationVerifier(issuer_b)
        tp_issuer = TrustedPrincipalIssuer()
        tp_verifier = TrustedPrincipalVerifier(tp_issuer)
        executor = ToolExecutor(registry, auth_verifier=verifier_b,
                                trusted_principal_verifier=tp_verifier)
        ctx = RunContext(agent_id="a", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))
        # Token signed by issuer_a — verifier_b has issuer_b's key
        token = issuer_a.issue(tool_name="echo", principal_id="alice",
                               execution_id="exec-1", context_id="ctx-1")
        result = await executor.execute("echo", {"message": "hi"}, context=ctx,
                                        authorization=token, call_id="c1")
        assert not result.success
        assert spy.execute_count == 0


# =====================================================================
# H-17: context_id empty string normalization
# =====================================================================


class TestH17ContextIdNormalization:
    """context_id="" and context_id=None must be normalized identically."""

    @pytest.mark.asyncio
    async def test_context_id_empty_string_normalizes_to_execution_id(self):
        """context_id="" → normalized to execution_id → token verifiable."""
        runtime = _build_runtime()
        await runtime.startup()
        spy = _SpyTool("echo")
        runtime.register_tool(spy)
        identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
        agent = Agent(identity, runtime)
        # Pass a context with context_id="" (empty string)
        ctx = RunContext(agent_id=identity.agent_id, task="echo hi", context_id="")
        response = await agent.run("echo hi", context=ctx)
        # Tool should execute — context_id="" normalized to execution_id
        assert spy.execute_count == 1, f"Expected 1 execution, got {spy.execute_count}"
        # The context_id should have been normalized
        assert ctx.context_id == ctx.execution_id, (
            f"context_id should be normalized to execution_id, got {ctx.context_id!r}"
        )
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_context_id_none_normalizes_to_execution_id(self):
        """context_id=None → normalized to execution_id → token verifiable."""
        runtime = _build_runtime()
        await runtime.startup()
        spy = _SpyTool("echo")
        runtime.register_tool(spy)
        identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
        agent = Agent(identity, runtime)
        ctx = RunContext(agent_id=identity.agent_id, task="echo hi")  # context_id defaults to None
        response = await agent.run("echo hi", context=ctx)
        assert spy.execute_count == 1
        assert ctx.context_id == ctx.execution_id
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_context_id_explicit_value_not_overwritten(self):
        """context_id="my-ctx" → preserved, not normalized."""
        runtime = _build_runtime()
        await runtime.startup()
        spy = _SpyTool("echo")
        runtime.register_tool(spy)
        identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
        agent = Agent(identity, runtime)
        ctx = RunContext(agent_id=identity.agent_id, task="echo hi", context_id="my-ctx")
        response = await agent.run("echo hi", context=ctx)
        assert spy.execute_count == 1
        assert ctx.context_id == "my-ctx"  # preserved
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_token_cannot_be_reused_with_different_context_id(self):
        """Token bound to ctx-1 cannot be validated with ctx-2."""
        registry = ToolRegistry()
        spy = _SpyTool("echo")
        registry.register(spy)
        issuer = AuthorizationIssuer()
        verifier = AuthorizationVerifier(issuer)
        tp_issuer = TrustedPrincipalIssuer()
        tp_verifier = TrustedPrincipalVerifier(tp_issuer)
        executor = ToolExecutor(registry, auth_verifier=verifier,
                                trusted_principal_verifier=tp_verifier)
        # Token bound to ctx-1
        token = issuer.issue(tool_name="echo", principal_id="alice",
                             execution_id="exec-1", context_id="ctx-1")
        # But context has ctx-2
        ctx = RunContext(agent_id="a", task="t", execution_id="exec-1", context_id="ctx-2")
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))
        result = await executor.execute("echo", {"message": "hi"}, context=ctx,
                                        authorization=token, call_id="c1")
        assert not result.success
        assert spy.execute_count == 0
