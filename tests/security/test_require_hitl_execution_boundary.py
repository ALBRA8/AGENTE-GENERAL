"""Tarea 2 — Pruebas de integración con un ejecutor instrumentado.

Context
-------
The auditor asks: "it is not enough to check that the verdict is not
ALLOW — you must verify that DENY and REQUIRE_HITL verdicts do NOT
execute tools without a valid authorization."

This test suite wires up:
  - PolicyEngine (produces verdicts)
  - ToolExecutor (executes tools, gated by authorization)
  - AuthorizationIssuer + AuthorizationVerifier (issued/verify tokens)
  - TrustedPrincipalIssuer + TrustedPrincipalVerifier (sealed identities)
  - ApprovalEngine (HITL approval lifecycle)
  - A spy Tool that records every execute() call

The spy Tool is the instrumented executor — it counts how many times
``execute()`` was actually invoked (vs. how many times the
ToolExecutor's ``execute()`` was called but rejected).

Verifies:
  1. DENY verdict → tool does NOT execute (spy.execute_called == 0).
  2. REQUIRE_HITL verdict (without approval) → tool does NOT execute.
  3. REQUIRE_HITL verdict WITH a valid HITL approval → tool DOES execute
     (the approval converts the verdict to ALLOW, an AuthorizationToken
     is issued, and the executor accepts it).
  4. No authorization object at all → tool does NOT execute.
  5. A token bound to action A does NOT authorize action B.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from albra_seed.approvals import ApprovalEngine
from albra_seed.core.base import Tool, ToolContract
from albra_seed.core.types import (
    AutonomyLevel,
    PolicyDecision,
    RiskLevel,
    RunContext,
    ToolCall,
    ToolResult,
)
from albra_seed.policy import PolicyEngine, PolicyRule, PolicyRuleSet
from albra_seed.security.auth import AuthPrincipal
from albra_seed.security.auth_token import (
    AuthorizationIssuer,
    AuthorizationVerifier,
)
from albra_seed.security.trusted_principal import (
    TrustedPrincipalIssuer,
    TrustedPrincipalVerifier,
)
from albra_seed.tools.executor import ToolExecutor
from albra_seed.tools.registry import ToolRegistry


# =====================================================================
# Spy Tool — counts actual executions
# =====================================================================


class _SpyTool(Tool):
    """A tool that records every actual execute() invocation.

    The spy's ``execute_called`` counter is the source of truth for
    "did the tool really run?". If the ToolExecutor's authorization
    gate rejects the call, ``execute_called`` stays 0.
    """

    def __init__(self, name: str = "tool.external.http") -> None:
        self._name = name
        self.execute_called: int = 0
        self.last_arguments: dict[str, Any] | None = None
        self.contract = ToolContract(
            name=name,
            description="spy tool",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            timeout_seconds=5,
            is_dry_run_capable=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        self.execute_called += 1
        self.last_arguments = dict(arguments)
        return ToolResult(
            call_id="spy",
            success=True,
            output={"spy_ran": True, "args": arguments},
        )


# =====================================================================
# Test rig — wires PolicyEngine + ToolExecutor + ApprovalEngine
# =====================================================================


class _TestRig:
    """Wires up the full authorization stack with a spy tool."""

    def __init__(self) -> None:
        # Spy tool + registry.
        self.spy = _SpyTool()
        self.registry = ToolRegistry()
        self.registry.register(self.spy)

        # Crypto: TrustedPrincipal issuer (seals identities).
        self.tp_issuer = TrustedPrincipalIssuer()
        self.tp_verifier = TrustedPrincipalVerifier(self.tp_issuer)

        # Crypto: AuthorizationToken issuer + verifier.
        self.auth_issuer = AuthorizationIssuer()
        self.auth_verifier = AuthorizationVerifier(self.auth_issuer)

        # ToolExecutor with full secure mode (auth_verifier + tp_verifier).
        self.executor = ToolExecutor(
            self.registry,
            auth_verifier=self.auth_verifier,
            trusted_principal_verifier=self.tp_verifier,
        )

        # PolicyEngine (no autonomy_manager — uses inline comparison).
        self.policy = PolicyEngine(rule_set=PolicyRuleSet())

        # ApprovalEngine for HITL.
        self.approvals = ApprovalEngine()

        # The "current principal" — sealed by the tp_issuer.
        self.principal = AuthPrincipal(
            principal_id="agent:test-agent",
            kind="agent",
            identity="test-agent",
            roles=["agent"],
            auth_method="test",
            is_anonymous=False,
        )
        self.sealed_principal = self.tp_issuer.seal(self.principal)

    def make_context(
        self,
        *,
        autonomy: AutonomyLevel = AutonomyLevel.L0_OBSERVE,
        execution_id: str = "exec-1",
        context_id: str = "ctx-1",
    ) -> RunContext:
        ctx = RunContext(
            agent_id="test-agent",
            task="test",
            autonomy_level=autonomy,
            risk_level=RiskLevel.LOW,
            execution_id=execution_id,
            context_id=context_id,
        )
        ctx.verified_principal = self.sealed_principal
        return ctx


# =====================================================================
# Tests — execution boundary
# =====================================================================


class TestRequireHitlExecutionBoundary:
    """Verify DENY and REQUIRE_HITL verdicts do NOT execute tools."""

    @pytest.mark.asyncio
    async def test_deny_verdict_does_not_execute_tool(self) -> None:
        """A DENY verdict blocks execution — the spy tool's
        execute_called counter stays 0."""
        rig = _TestRig()
        # Add a DENY rule for the spy's action.
        rig.policy.add_rule(
            PolicyRule(
                name="deny_external",
                action_pattern="tool.external.*",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=AutonomyLevel.L5_SELF_IMPROVE,
                decision=PolicyDecision.DENY,
                priority=100,
            )
        )

        ctx = rig.make_context(autonomy=AutonomyLevel.L5_SELF_IMPROVE)
        call = ToolCall(tool_name="tool.external.http", arguments={"url": "x"})

        # Policy says DENY.
        verdict = await rig.policy.evaluate(call, context=ctx)
        assert verdict.decision == PolicyDecision.DENY

        # Even if we tried to issue a token (we should NOT), the executor
        # must reject because verdict.allowed is False. We pass the
        # verdict as authorization — the executor checks verdict.allowed.
        result = await rig.executor.execute(
            "tool.external.http",
            {"url": "x"},
            context=ctx,
            authorization=verdict,  # DENY verdict — allowed=False
        )
        assert result.success is False
        assert result.output.get("not_authorized") is True
        assert rig.spy.execute_called == 0, (
            f"DENY verdict should NOT execute the tool; "
            f"spy was called {rig.spy.execute_called} times"
        )

    @pytest.mark.asyncio
    async def test_require_hitl_verdict_does_not_execute_without_approval(self) -> None:
        """A REQUIRE_HITL verdict (without a human approval) does NOT
        execute the tool. The spy's execute_called stays 0."""
        rig = _TestRig()
        rig.policy.add_rule(
            PolicyRule(
                name="allow_external_l3",
                action_pattern="tool.external.*",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=AutonomyLevel.L3_EXECUTE_EXTERNAL,
                decision=PolicyDecision.ALLOW,
                priority=10,
            )
        )

        ctx = rig.make_context(autonomy=AutonomyLevel.L0_OBSERVE)
        call = ToolCall(tool_name="tool.external.http", arguments={"url": "x"})

        verdict = await rig.policy.evaluate(call, context=ctx)
        assert verdict.decision == PolicyDecision.REQUIRE_HITL
        assert verdict.allowed is False  # REQUIRE_HITL is NOT allow

        # Pass the REQUIRE_HITL verdict to the executor — it must reject
        # because verdict.allowed is False (no AuthorizationToken issued).
        result = await rig.executor.execute(
            "tool.external.http",
            {"url": "x"},
            context=ctx,
            authorization=verdict,  # REQUIRE_HITL — allowed=False
        )
        assert result.success is False
        assert rig.spy.execute_called == 0, (
            f"REQUIRE_HITL without approval should NOT execute; "
            f"spy was called {rig.spy.execute_called} times"
        )

    @pytest.mark.asyncio
    async def test_require_hitl_with_valid_approval_executes_tool(self) -> None:
        """A REQUIRE_HITL verdict + a valid HITL approval → tool DOES
        execute (the approval converts the verdict to ALLOW, an
        AuthorizationToken is issued, the executor accepts it)."""
        rig = _TestRig()
        rig.policy.add_rule(
            PolicyRule(
                name="allow_external_l3",
                action_pattern="tool.external.*",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=AutonomyLevel.L3_EXECUTE_EXTERNAL,
                decision=PolicyDecision.ALLOW,
                priority=10,
            )
        )

        ctx = rig.make_context(autonomy=AutonomyLevel.L0_OBSERVE)
        call = ToolCall(tool_name="tool.external.http", arguments={"url": "x"})

        # Step 1: policy says REQUIRE_HITL.
        verdict = await rig.policy.evaluate(call, context=ctx)
        assert verdict.decision == PolicyDecision.REQUIRE_HITL

        # Step 2: request HITL approval, bound to the action.
        approval = await rig.approvals.request(call, context=ctx)
        decision = await rig.approvals.approve(
            approval.approval_id, approver="human:alice"
        )
        assert decision.status == "approved"

        # Step 3: human approval converts REQUIRE_HITL → ALLOW.
        # The caller issues an AuthorizationToken (the policy verdict
        # is now considered ALLOW because the human approved).
        # NOTE: the token's principal_id must match the SEALED
        # TrustedPrincipal's principal_id (which is derived from the
        # AuthPrincipal's `identity` field, not its `principal_id`).
        # The executor extracts principal_id from verified_principal
        # and verifies the token against THAT value.
        token = rig.auth_issuer.issue(
            tool_name="tool.external.http",
            principal_id=rig.sealed_principal.principal_id,
            execution_id=ctx.execution_id,
            context_id=ctx.context_id,
        )

        # Step 4: executor accepts the token → tool executes.
        result = await rig.executor.execute(
            "tool.external.http",
            {"url": "x"},
            context=ctx,
            authorization=token,
        )
        assert result.success is True
        assert rig.spy.execute_called == 1, (
            f"Valid HITL approval should execute the tool exactly once; "
            f"spy was called {rig.spy.execute_called} times"
        )
        assert rig.spy.last_arguments == {"url": "x"}

    @pytest.mark.asyncio
    async def test_no_authorization_object_does_not_execute(self) -> None:
        """No authorization object at all → tool does NOT execute
        (fail-closed at the executor's authorization gate)."""
        rig = _TestRig()

        ctx = rig.make_context()

        result = await rig.executor.execute(
            "tool.external.http",
            {"url": "x"},
            context=ctx,
            authorization=None,  # no authorization at all
        )
        assert result.success is False
        assert result.output.get("not_authorized") is True
        assert rig.spy.execute_called == 0

    @pytest.mark.asyncio
    async def test_token_bound_to_action_a_does_not_authorize_action_b(self) -> None:
        """A token issued for action A does NOT authorize action B.

        The AuthorizationToken is bound to (tool_name, execution_id,
        principal_id, context_id). The verifier checks all four. A
        token for tool.external.http must NOT authorize tool.external.smtp.
        """
        rig = _TestRig()

        # Register a second spy for action B.
        spy_b = _SpyTool(name="tool.external.smtp")
        rig.registry.register(spy_b)

        ctx = rig.make_context()

        # Issue a token for action A.
        token_a = rig.auth_issuer.issue(
            tool_name="tool.external.http",
            principal_id=rig.sealed_principal.principal_id,
            execution_id=ctx.execution_id,
            context_id=ctx.context_id,
        )

        # Try to use token A for action B — must fail.
        result_b = await rig.executor.execute(
            "tool.external.smtp",
            {"to": "b"},
            context=ctx,
            authorization=token_a,  # token bound to action A
        )
        assert result_b.success is False
        assert result_b.output.get("not_authorized") is True
        assert spy_b.execute_called == 0, (
            f"Token for action A should NOT execute action B; "
            f"spy_b was called {spy_b.execute_called} times"
        )

        # The same token DOES authorize action A.
        result_a = await rig.executor.execute(
            "tool.external.http",
            {"url": "a"},
            context=ctx,
            authorization=token_a,
        )
        assert result_a.success is True
        assert rig.spy.execute_called == 1

    @pytest.mark.asyncio
    async def test_expired_token_does_not_execute(self) -> None:
        """An expired AuthorizationToken does NOT execute the tool."""
        rig = _TestRig()

        ctx = rig.make_context()

        # Issue a token with TTL=0 (immediately expired).
        token = rig.auth_issuer.issue(
            tool_name="tool.external.http",
            principal_id=rig.sealed_principal.principal_id,
            execution_id=ctx.execution_id,
            context_id=ctx.context_id,
            ttl_seconds=0.0,
        )

        # Wait a moment so the token is definitely expired.
        await asyncio.sleep(0.01)

        result = await rig.executor.execute(
            "tool.external.http",
            {"url": "x"},
            context=ctx,
            authorization=token,
        )
        assert result.success is False
        assert result.output.get("not_authorized") is True
        assert rig.spy.execute_called == 0

    @pytest.mark.asyncio
    async def test_revoked_token_does_not_execute(self) -> None:
        """A revoked AuthorizationToken does NOT execute the tool."""
        rig = _TestRig()

        ctx = rig.make_context()

        token = rig.auth_issuer.issue(
            tool_name="tool.external.http",
            principal_id=rig.sealed_principal.principal_id,
            execution_id=ctx.execution_id,
            context_id=ctx.context_id,
        )

        # Revoke the token.
        rig.auth_issuer.revoke(token.token_id)

        result = await rig.executor.execute(
            "tool.external.http",
            {"url": "x"},
            context=ctx,
            authorization=token,
        )
        assert result.success is False
        assert result.output.get("not_authorized") is True
        assert rig.spy.execute_called == 0

    @pytest.mark.asyncio
    async def test_forged_principal_does_not_execute(self) -> None:
        """A forged (unsealed) AuthPrincipal does NOT execute the tool.

        The executor's TrustedPrincipal verification rejects any object
        that is not a properly sealed TrustedPrincipal.
        """
        rig = _TestRig()

        ctx = rig.make_context()
        # Replace the verified_principal with a raw AuthPrincipal (forged).
        ctx.verified_principal = AuthPrincipal(
            principal_id="agent:attacker",
            kind="agent",
            identity="attacker",
            roles=["agent"],
            auth_method="forged",
            is_anonymous=False,
        )

        token = rig.auth_issuer.issue(
            tool_name="tool.external.http",
            principal_id="agent:attacker",  # matches the forged principal
            execution_id=ctx.execution_id,
            context_id=ctx.context_id,
        )

        result = await rig.executor.execute(
            "tool.external.http",
            {"url": "x"},
            context=ctx,
            authorization=token,
        )
        assert result.success is False
        # The error should mention the principal is not a sealed TrustedPrincipal.
        assert "not_authorized" in result.output
        assert rig.spy.execute_called == 0


__all__ = ["TestRequireHitlExecutionBoundary"]
