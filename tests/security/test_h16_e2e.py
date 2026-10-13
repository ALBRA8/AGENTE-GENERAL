"""H-16 end-to-end: external issuer + verifier compatible → legitimate execution.

Also verifies: incompatible issuer → rejected, forged token → rejected,
no execution when validation fails.
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
    def __init__(self, name="echo"):
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


def _build_runtime():
    runtime = Runtime(config=SeedConfig())
    runtime.register_provider(_ToolCallingProvider([
        ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})
    ]))
    spy = _SpyTool("echo")
    runtime.register_tool(spy)
    fw = ActionFirewall()
    fw.add_rule(FirewallRule(action_pattern="echo", actor_pattern="*", decision="allow",
                             risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0))
    runtime.register("firewall", fw)
    policy = PolicyEngine()
    policy.add_rule(PolicyRule(name="allow_echo", action_pattern="echo", actor_pattern="*",
                               risk_threshold=RiskLevel.UNKNOWN, autonomy_required=AutonomyLevel.L0_OBSERVE,
                               decision="allow", priority=10))
    runtime.register("policy_engine", policy)
    return runtime, spy


@pytest.mark.asyncio
async def test_h16_external_issuer_and_verifier_compatible_executes():
    """External issuer + verifier from the same authority → legitimate execution."""
    runtime, spy = _build_runtime()
    await runtime.startup()

    # External issuer + verifier (compatible — derived from the same issuer)
    ext_issuer = AuthorizationIssuer()
    ext_verifier = AuthorizationVerifier(ext_issuer)

    identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
    agent = Agent(identity, runtime, auth_issuer=ext_issuer, auth_verifier=ext_verifier)

    response = await agent.run("echo hi")

    # Tool executed exactly once — the agent used the external issuer to
    # issue tokens, and the executor used the external verifier to validate.
    assert spy.execute_count == 1, f"Expected 1 execution, got {spy.execute_count}"
    assert response.tool_results[0].success is True

    await runtime.shutdown()


@pytest.mark.asyncio
async def test_h16_external_verifier_only_fails_closed():
    """External verifier without issuer → fail-closed, no execution."""
    runtime, spy = _build_runtime()
    await runtime.startup()

    ext_issuer = AuthorizationIssuer()  # different key
    ext_verifier = AuthorizationVerifier(ext_issuer)

    identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
    agent = Agent(identity, runtime, auth_verifier=ext_verifier)  # NO auth_issuer

    response = await agent.run("echo hi")

    assert spy.execute_count == 0, f"Expected 0 executions (fail-closed), got {spy.execute_count}"
    assert response.tool_results[0].success is False

    await runtime.shutdown()


@pytest.mark.asyncio
async def test_h16_incompatible_issuer_verifier_fails_closed():
    """Issuer A + verifier B (different keys) → tokens rejected."""
    runtime, spy = _build_runtime()
    await runtime.startup()

    issuer_a = AuthorizationIssuer()
    issuer_b = AuthorizationIssuer()  # different key
    verifier_b = AuthorizationVerifier(issuer_b)

    identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
    # Agent uses issuer_a to issue tokens, but executor has verifier_b
    agent = Agent(identity, runtime, auth_issuer=issuer_a, auth_verifier=verifier_b)

    response = await agent.run("echo hi")

    assert spy.execute_count == 0, f"Expected 0 executions (incompatible keys), got {spy.execute_count}"
    assert response.tool_results[0].success is False

    await runtime.shutdown()


@pytest.mark.asyncio
async def test_h16_no_external_pair_self_created_works():
    """No external issuer/verifier → agent creates its own pair → works."""
    runtime, spy = _build_runtime()
    await runtime.startup()

    identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
    agent = Agent(identity, runtime)  # no auth_issuer, no auth_verifier

    response = await agent.run("echo hi")

    assert spy.execute_count == 1
    assert response.tool_results[0].success is True

    await runtime.shutdown()


@pytest.mark.asyncio
async def test_h16_forged_token_rejected():
    """A forged token (not from the issuer) → rejected, no execution."""
    runtime, spy = _build_runtime()
    await runtime.startup()

    ext_issuer = AuthorizationIssuer()
    ext_verifier = AuthorizationVerifier(ext_issuer)

    identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
    agent = Agent(identity, runtime, auth_issuer=ext_issuer, auth_verifier=ext_verifier)

    response = await agent.run("echo hi")

    # The agent issued a valid token using ext_issuer, so the call succeeds
    assert spy.execute_count == 1

    # Now try a FORGED token directly on the executor
    other_issuer = AuthorizationIssuer()
    forged_token = other_issuer.issue(
        tool_name="echo", principal_id="alice",
        execution_id="fake-exec", context_id="fake-ctx",
    )
    tp_issuer = TrustedPrincipalIssuer()
    tp_verifier = TrustedPrincipalVerifier(tp_issuer)
    registry = ToolRegistry()
    spy2 = _SpyTool("echo")
    registry.register(spy2)
    executor = ToolExecutor(registry, auth_verifier=ext_verifier,
                            trusted_principal_verifier=tp_verifier)
    ctx = RunContext(agent_id="a", task="t", execution_id="fake-exec", context_id="fake-ctx")
    ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))
    result = await executor.execute("echo", {"message": "hi"}, context=ctx,
                                    authorization=forged_token, call_id="c1")
    assert not result.success
    assert spy2.execute_count == 0

    await runtime.shutdown()
