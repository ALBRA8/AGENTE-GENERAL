"""Adversarial tests for the 3 consolidated defects.

DEFECT 1: Forged authorization must be rejected by ToolExecutor.
DEFECT 2: Dry-run must guarantee no side effects for non-dry-run-capable tools.
DEFECT 3: Budget must be reserved before dispatch — excess calls rejected.

Each test verifies BOTH the decision AND the observable effect.
"""
from __future__ import annotations

import asyncio
import pytest
import time
from typing import Any

from albra_seed import (
    Agent, AgentIdentity, Runtime, SeedConfig,
    AutonomyLevel, RiskLevel, RunContext, Session,
)
from albra_seed.core.types import ToolCall, AgentResponse, Message, Role, ExecutionStatus
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.memory.manager import MemoryManager
from albra_seed.providers.mock import MockProvider
from albra_seed.tools.infra_tools import EchoTool
from albra_seed.tools.executor import ToolExecutor
from albra_seed.tools.registry import ToolRegistry
from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule
from albra_seed.security.auth_token import (
    AuthorizationToken, AuthorizationIssuer, AuthorizationVerifier,
)
from albra_seed.security.auth import AuthPrincipal
from albra_seed.security.trusted_principal import TrustedPrincipal, TrustedPrincipalIssuer, TrustedPrincipalVerifier
from albra_seed.core.base import ToolContract


# =====================================================================
# Helpers
# =====================================================================


class _SpyTool(EchoTool):
    """EchoTool that tracks every execute() call — even dry_run."""
    def __init__(self):
        super().__init__()
        self.execute_count = 0
        self.dry_run_count = 0

    async def execute(self, arguments, *, context=None, dry_run=False):
        self.execute_count += 1
        if dry_run:
            self.dry_run_count += 1
        # Simulate a side effect even in dry_run mode (to prove the
        # executor MUST reject this, not just pass dry_run=True and hope)
        return await super().execute(arguments, context=context, dry_run=dry_run)


def _make_spy_contract(name: str = "spy") -> ToolContract:
    """A tool contract for the spy tool."""
    return ToolContract(
        name=name,
        description="Spy tool for testing",
        input_schema={"message": {"type": "string"}},
        output_schema={},
        risk_level=RiskLevel.LOW,
        permissions=["tool.execute.spy"],
        timeout_seconds=30,
        retry_policy={"max": 0},
        evidence_required=False,
        is_dry_run_capable=False,  # NOT dry-run capable by default
        is_external=False,
        availability="available",
    )


def _build_registry_with_spy(*, dry_run_capable: bool = False) -> tuple[ToolRegistry, _SpyTool]:
    """Build a ToolRegistry with a spy tool."""
    registry = ToolRegistry()
    spy = _SpyTool()
    contract = _make_spy_contract()
    if dry_run_capable:
        contract = contract.model_copy(update={"is_dry_run_capable": True})
    spy.contract = contract
    registry.register(spy)
    return registry, spy


# =====================================================================
# DEFECT 1: Forged authorization rejected by ToolExecutor
# =====================================================================


class TestForgedAuthorization:
    """Forged authorization objects with allowed=True must be rejected."""

    @pytest.mark.asyncio
    async def test_forged_object_with_allowed_true_rejected(self):
        """A plain object with allowed=True must NOT authorize execution."""
        registry, spy = _build_registry_with_spy()
        issuer = AuthorizationIssuer()
        verifier = AuthorizationVerifier(issuer)
        tp_issuer = TrustedPrincipalIssuer()
        executor = ToolExecutor(
            registry,
            auth_verifier=verifier,
            trusted_principal_verifier=TrustedPrincipalVerifier(tp_issuer),
        )

        # Forge an authorization object
        class _ForgedAuth:
            allowed = True
            decision = "allow"

        ctx = RunContext(agent_id="test", task="t")
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", roles=["admin"], is_anonymous=False))

        result = await executor.execute(
            "spy", {"message": "hi"},
            context=ctx,
            authorization=_ForgedAuth(),
            call_id="call-1",
        )

        # REJECTED — forged authorization is not a valid AuthorizationToken
        assert result.success is False
        assert "not authorized" in result.error.lower()
        assert spy.execute_count == 0, f"Spy executed {spy.execute_count} times!"

    @pytest.mark.asyncio
    async def test_valid_authorization_token_accepted(self):
        """A properly issued AuthorizationToken is accepted."""
        registry, spy = _build_registry_with_spy()
        issuer = AuthorizationIssuer()
        verifier = AuthorizationVerifier(issuer)
        tp_issuer = TrustedPrincipalIssuer()
        executor = ToolExecutor(
            registry,
            auth_verifier=verifier,
            trusted_principal_verifier=TrustedPrincipalVerifier(tp_issuer),
        )

        ctx = RunContext(agent_id="test", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", roles=["admin"], is_anonymous=False))

        token = issuer.issue(
            tool_name="spy",
            principal_id="alice",
            execution_id="exec-1",
            context_id='ctx-1',
        )

        result = await executor.execute(
            "spy", {"message": "hi"},
            context=ctx,
            authorization=token,
            call_id="call-1",
        )

        assert result.success is True
        assert spy.execute_count == 1

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self):
        """An expired token is rejected."""
        registry, spy = _build_registry_with_spy()
        issuer = AuthorizationIssuer(ttl_seconds=0.01)
        verifier = AuthorizationVerifier(issuer)
        tp_issuer = TrustedPrincipalIssuer()
        executor = ToolExecutor(
            registry,
            auth_verifier=verifier,
            trusted_principal_verifier=TrustedPrincipalVerifier(tp_issuer),
        )

        ctx = RunContext(agent_id="test", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))

        token = issuer.issue(
            tool_name="spy",
            principal_id="alice",
            execution_id="exec-1",
        )
        # Wait for expiry
        await asyncio.sleep(0.02)

        result = await executor.execute(
            "spy", {"message": "hi"},
            context=ctx,
            authorization=token,
            call_id="call-1",
        )

        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_token_bound_to_different_tool_rejected(self):
        """A token for tool A cannot authorize tool B."""
        registry, spy = _build_registry_with_spy()
        issuer = AuthorizationIssuer()
        verifier = AuthorizationVerifier(issuer)
        tp_issuer = TrustedPrincipalIssuer()
        executor = ToolExecutor(
            registry,
            auth_verifier=verifier,
            trusted_principal_verifier=TrustedPrincipalVerifier(tp_issuer),
        )

        ctx = RunContext(agent_id="test", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))

        # Issue token for "echo" but try to execute "spy"
        token = issuer.issue(
            tool_name="echo",
            principal_id="alice",
            execution_id="exec-1",
        )

        result = await executor.execute(
            "spy", {"message": "hi"},
            context=ctx,
            authorization=token,
            call_id="call-1",
        )

        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_token_bound_to_different_execution_rejected(self):
        """A token for execution A cannot authorize execution B."""
        registry, spy = _build_registry_with_spy()
        issuer = AuthorizationIssuer()
        verifier = AuthorizationVerifier(issuer)
        tp_issuer = TrustedPrincipalIssuer()
        executor = ToolExecutor(
            registry,
            auth_verifier=verifier,
            trusted_principal_verifier=TrustedPrincipalVerifier(tp_issuer),
        )

        ctx = RunContext(agent_id="test", task="t", execution_id="exec-A")
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))

        # Issue token for exec-B but try to execute in exec-A
        token = issuer.issue(
            tool_name="spy",
            principal_id="alice",
            execution_id="exec-B",
        )

        result = await executor.execute(
            "spy", {"message": "hi"},
            context=ctx,
            authorization=token,
            call_id="call-1",
        )

        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_token_bound_to_different_principal_rejected(self):
        """A token for principal A cannot authorize principal B."""
        registry, spy = _build_registry_with_spy()
        issuer = AuthorizationIssuer()
        verifier = AuthorizationVerifier(issuer)
        tp_issuer = TrustedPrincipalIssuer()
        executor = ToolExecutor(
            registry,
            auth_verifier=verifier,
            trusted_principal_verifier=TrustedPrincipalVerifier(tp_issuer),
        )

        ctx = RunContext(agent_id="test", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="bob", is_anonymous=False))

        # Issue token for alice but execute as bob
        token = issuer.issue(
            tool_name="spy",
            principal_id="alice",
            execution_id="exec-1",
        )

        result = await executor.execute(
            "spy", {"message": "hi"},
            context=ctx,
            authorization=token,
            call_id="call-1",
        )

        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_revoked_token_rejected(self):
        """A revoked token is rejected."""
        registry, spy = _build_registry_with_spy()
        issuer = AuthorizationIssuer()
        verifier = AuthorizationVerifier(issuer)
        tp_issuer = TrustedPrincipalIssuer()
        executor = ToolExecutor(
            registry,
            auth_verifier=verifier,
            trusted_principal_verifier=TrustedPrincipalVerifier(tp_issuer),
        )

        ctx = RunContext(agent_id="test", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))

        token = issuer.issue(
            tool_name="spy",
            principal_id="alice",
            execution_id="exec-1",
        )
        issuer.revoke(token.token_id)

        result = await executor.execute(
            "spy", {"message": "hi"},
            context=ctx,
            authorization=token,
            call_id="call-1",
        )

        assert result.success is False
        assert spy.execute_count == 0


# =====================================================================
# DEFECT 2: Dry-run must guarantee no side effects
# =====================================================================


class TestDryRunSafety:
    """Dry-run must guarantee absence of effects for non-dry-run-capable tools."""

    @pytest.mark.asyncio
    async def test_dry_run_non_capable_tool_rejected(self):
        """A tool NOT declared is_dry_run_capable → dry-run rejected."""
        registry, spy = _build_registry_with_spy(dry_run_capable=False)
        executor = ToolExecutor(registry)

        result = await executor.execute(
            "spy", {"message": "hi"},
            dry_run=True,
        )

        assert result.success is False
        assert "not_dry_run_capable" in (result.output.get("reason", "") if result.output else "")
        assert spy.execute_count == 0, "Spy was called despite dry_run rejection!"
        assert spy.dry_run_count == 0

    @pytest.mark.asyncio
    async def test_dry_run_capable_tool_executes_safely(self):
        """A tool declared is_dry_run_capable → dry-run returns simulation."""
        registry, spy = _build_registry_with_spy(dry_run_capable=True)
        executor = ToolExecutor(registry)

        result = await executor.execute(
            "spy", {"message": "hi"},
            dry_run=True,
        )

        assert result.success is True
        # The executor returns a SIMULATED result WITHOUT invoking
        # tool.execute() — zero side effects guaranteed.
        assert spy.execute_count == 0, f"Spy executed {spy.execute_count} times during dry-run!"
        assert spy.dry_run_count == 0
        # The simulation output describes what WOULD have happened
        assert result.output.get("simulated") is True
        assert result.output.get("dry_run") is True

    @pytest.mark.asyncio
    async def test_real_execution_without_authorization_rejected(self):
        """Non-dry-run without authorization → rejected."""
        registry, spy = _build_registry_with_spy()
        executor = ToolExecutor(registry)

        result = await executor.execute(
            "spy", {"message": "hi"},
            dry_run=False,
        )

        assert result.success is False
        assert "not authorized" in result.error.lower()
        assert spy.execute_count == 0


# =====================================================================
# DEFECT 3: Budget reserved before dispatch
# =====================================================================


class _CountingProvider(MockProvider):
    """Provider that emits N tool_calls per iteration."""
    def __init__(self, tool_name: str, calls_per_iter: int, total_iters: int = 100):
        super().__init__()
        self._tool_name = tool_name
        self._calls_per_iter = calls_per_iter
        self._total_iters = total_iters
        self._iter = 0

    async def complete(self, messages, **kwargs):
        self._call_count += 1
        self._iter += 1
        ctx = kwargs.get("context")
        if self._iter > self._total_iters:
            return AgentResponse(
                content="[mock] done",
                execution_id=ctx.execution_id if ctx else "x",
                correlation_id=ctx.correlation_id if ctx else "y",
            )
        # Emit N tool_calls with fresh ids each iteration
        tool_calls = [
            ToolCall(
                id=f"tc-{self._iter}-{i}",
                tool_name=self._tool_name,
                arguments={"message": f"call-{i}"},
            )
            for i in range(self._calls_per_iter)
        ]
        return AgentResponse(
            content="[mock] tool calls",
            execution_id=ctx.execution_id if ctx else "x",
            correlation_id=ctx.correlation_id if ctx else "y",
            tool_calls=tool_calls,
        )


class TestBudgetReservation:
    """Budget must be reserved before each dispatch."""

    @pytest.mark.asyncio
    async def test_plan_exceeding_budget_blocks_excess_calls(self):
        """A plan with more calls than max_tool_calls → excess calls NOT executed."""
        from albra_seed.runtime.loop import CognitiveLoopConfig

        runtime = Runtime(config=SeedConfig())
        await runtime.startup()

        # Provider emits 5 tool calls per iteration
        provider = _CountingProvider("counter", 5, total_iters=100)
        runtime.register_provider(provider)

        # Register a counting tool
        execute_count = [0]
        class _CounterTool(EchoTool):
            async def execute(self, arguments, *, context=None, dry_run=False):
                execute_count[0] += 1
                return await super().execute(arguments, context=context, dry_run=dry_run)
        tool = _CounterTool()
        tool.contract = ToolContract(
            name="counter", description="counter",
            input_schema={"message": {"type": "string"}}, output_schema={},
            risk_level=RiskLevel.LOW, permissions=[], timeout_seconds=30,
            retry_policy={"max": 0}, evidence_required=False,
            is_dry_run_capable=False, is_external=False, availability="available",
        )
        runtime.register_tool(tool)

        fw = ActionFirewall()
        fw.add_rule(FirewallRule(action_pattern="counter", actor_pattern="*", decision="allow",
                                 risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0))
        runtime.register("firewall", fw)
        policy = PolicyEngine()
        policy.add_rule(PolicyRule(name="allow_counter", action_pattern="counter", actor_pattern="*",
                                   risk_threshold=RiskLevel.UNKNOWN, autonomy_required=AutonomyLevel.L0_OBSERVE,
                                   decision="allow", priority=10))
        runtime.register("policy_engine", policy)

        identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
        agent = Agent(identity, runtime, cognitive_loop_config=CognitiveLoopConfig(
            max_iterations=100, max_tool_calls=3, max_calls_per_tool=100,
        ))

        response = await agent.run("loop")
        ctx = agent.state.current_context

        # Only 3 calls should have been executed (budget=3)
        assert execute_count[0] == 3, f"Expected 3 executions, got {execute_count[0]}"
        assert ctx.cognitive_tool_calls == 3
        assert any("max_tool_calls" in w for w in ctx.warnings)

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_zero_budget_blocks_all_calls(self):
        """max_tool_calls=0 → no calls executed."""
        from albra_seed.runtime.loop import CognitiveLoopConfig

        runtime = Runtime(config=SeedConfig())
        await runtime.startup()

        provider = _CountingProvider("counter", 3, total_iters=100)
        runtime.register_provider(provider)

        execute_count = [0]
        class _CounterTool(EchoTool):
            async def execute(self, arguments, *, context=None, dry_run=False):
                execute_count[0] += 1
                return await super().execute(arguments, context=context, dry_run=dry_run)
        tool = _CounterTool()
        tool.contract = ToolContract(
            name="counter", description="counter",
            input_schema={"message": {"type": "string"}}, output_schema={},
            risk_level=RiskLevel.LOW, permissions=[], timeout_seconds=30,
            retry_policy={"max": 0}, evidence_required=False,
            is_dry_run_capable=False, is_external=False, availability="available",
        )
        runtime.register_tool(tool)

        fw = ActionFirewall()
        fw.add_rule(FirewallRule(action_pattern="counter", actor_pattern="*", decision="allow",
                                 risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0))
        runtime.register("firewall", fw)
        policy = PolicyEngine()
        policy.add_rule(PolicyRule(name="allow", action_pattern="counter", actor_pattern="*",
                                   risk_threshold=RiskLevel.UNKNOWN, autonomy_required=AutonomyLevel.L0_OBSERVE,
                                   decision="allow", priority=10))
        runtime.register("policy_engine", policy)

        identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
        agent = Agent(identity, runtime, cognitive_loop_config=CognitiveLoopConfig(
            max_iterations=1, max_tool_calls=0, max_calls_per_tool=100,
        ))

        response = await agent.run("loop")
        ctx = agent.state.current_context

        assert execute_count[0] == 0, f"Expected 0 executions, got {execute_count[0]}"
        assert ctx.cognitive_tool_calls == 0

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_exact_budget_executes_all(self):
        """max_tool_calls=N → exactly N calls execute (no more, no less)."""
        from albra_seed.runtime.loop import CognitiveLoopConfig

        runtime = Runtime(config=SeedConfig())
        await runtime.startup()

        provider = _CountingProvider("counter", 2, total_iters=100)
        runtime.register_provider(provider)

        execute_count = [0]
        class _CounterTool(EchoTool):
            async def execute(self, arguments, *, context=None, dry_run=False):
                execute_count[0] += 1
                return await super().execute(arguments, context=context, dry_run=dry_run)
        tool = _CounterTool()
        tool.contract = ToolContract(
            name="counter", description="counter",
            input_schema={"message": {"type": "string"}}, output_schema={},
            risk_level=RiskLevel.LOW, permissions=[], timeout_seconds=30,
            retry_policy={"max": 0}, evidence_required=False,
            is_dry_run_capable=False, is_external=False, availability="available",
        )
        runtime.register_tool(tool)

        fw = ActionFirewall()
        fw.add_rule(FirewallRule(action_pattern="counter", actor_pattern="*", decision="allow",
                                 risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0))
        runtime.register("firewall", fw)
        policy = PolicyEngine()
        policy.add_rule(PolicyRule(name="allow", action_pattern="counter", actor_pattern="*",
                                   risk_threshold=RiskLevel.UNKNOWN, autonomy_required=AutonomyLevel.L0_OBSERVE,
                                   decision="allow", priority=10))
        runtime.register("policy_engine", policy)

        identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
        agent = Agent(identity, runtime, cognitive_loop_config=CognitiveLoopConfig(
            max_iterations=100, max_tool_calls=4, max_calls_per_tool=100,
        ))

        response = await agent.run("loop")
        ctx = agent.state.current_context

        assert execute_count[0] == 4, f"Expected 4 executions, got {execute_count[0]}"
        assert ctx.cognitive_tool_calls == 4

        await runtime.shutdown()
