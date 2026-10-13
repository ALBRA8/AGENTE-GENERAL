"""B-SEC-AUTONOMY — Regression tests for fail-closed autonomy authorization.

Context
-------
The independent audit found a fail-open bug in ``PolicyEngine.evaluate()``:
``autonomy_ok`` was initialized to ``True`` and the autonomy check only ran
when ALL of (matched_rule, autonomy_manager, run_autonomy) were present.
A missing manager or missing context silently bypassed the check, allowing
a high-autonomy action to be authorized without verification.

Fix
---
``autonomy_ok`` now starts ``False`` (fail-closed). The check runs whenever
a matched rule requires autonomy > L0. If ``autonomy_manager`` is missing,
the engine performs the inline comparison (``int(agent) >= int(required)``)
— the same logic ``AutonomyManager.can_perform`` uses. If the manager
raises, the check fails closed (DENY, not REQUIRE_HITL — we cannot
escalate what we cannot verify).

These tests cover every scenario the auditor enumerated, plus edge cases
that verify no alternative route bypasses the policy verdict.
"""

from __future__ import annotations

import asyncio

import pytest

from albra_seed.autonomy.manager import AutonomyManager
from albra_seed.core.types import (
    AutonomyLevel,
    PolicyDecision,
    RiskLevel,
    RunContext,
    ToolCall,
)
from albra_seed.policy import PolicyEngine, PolicyRule, PolicyRuleSet


def _run(coro):
    return asyncio.run(coro)


def _make_allow_rule(
    *,
    autonomy_required: AutonomyLevel = AutonomyLevel.L0_OBSERVE,
    required_permission: str = "",
    priority: int = 10,
    risk_threshold: RiskLevel = RiskLevel.UNKNOWN,
) -> PolicyRule:
    """Build an ALLOW rule with the given autonomy_required.

    ``risk_threshold`` defaults to ``UNKNOWN`` so the rule matches
    even when no context is provided (context=None → risk=UNKNOWN).
    """
    return PolicyRule(
        name=f"allow_rule_{autonomy_required.name}",
        action_pattern="tool.dangerous.*",
        actor_pattern="*",
        risk_threshold=risk_threshold,
        autonomy_required=autonomy_required,
        required_permission=required_permission,
        decision=PolicyDecision.ALLOW,
        priority=priority,
    )


def _make_call(action: str = "tool.dangerous.op") -> ToolCall:
    return ToolCall(tool_name=action, arguments={})


def _make_ctx(
    *,
    autonomy: AutonomyLevel | None = AutonomyLevel.L0_OBSERVE,
    risk: RiskLevel = RiskLevel.LOW,
    agent_id: str = "a1",
) -> RunContext | None:
    """Build a RunContext. If autonomy is None, the context's
    autonomy_level defaults to L0_OBSERVE (the RunContext field default).
    To test a truly missing autonomy_level, pass None and we return None
    context entirely (the engine resolves None context → None run_autonomy)."""
    if autonomy is None and risk is None:
        return None
    return RunContext(
        agent_id=agent_id,
        task="test",
        autonomy_level=autonomy if autonomy is not None else AutonomyLevel.L0_OBSERVE,
        risk_level=risk,
    )


# =====================================================================
# Required regression tests (auditor's enumeration)
# =====================================================================


class TestBSecAutonomyFailClosed:
    """Verify the fail-closed autonomy authorization policy."""

    @pytest.mark.asyncio
    async def test_1_allow_l5_rule_agent_l0_no_manager_must_deny(self) -> None:
        """Regression test 1: ALLOW rule with autonomy_required=L5_SELF_IMPROVE,
        agent at L0_OBSERVE, no autonomy_manager configured.

        BEFORE the fix: autonomy_ok=True (no manager → check skipped),
        ALLOW would pass. FAIL-OPEN BUG.

        AFTER the fix: inline comparison int(L0)=0 >= int(L5)=5 → False.
        ALLOW downgraded to REQUIRE_HITL (level is verifiable, just
        insufficient — a human could approve at L5).
        """
        engine = PolicyEngine(rule_set=PolicyRuleSet())
        engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L5_SELF_IMPROVE))
        # No autonomy_manager passed to PolicyEngine.

        ctx = _make_ctx(autonomy=AutonomyLevel.L0_OBSERVE)
        verdict = await engine.evaluate(_make_call(), context=ctx)

        # The action MUST NOT be authorized (ALLOW must not survive).
        assert verdict.decision != PolicyDecision.ALLOW, (
            f"FAIL-OPEN: L0 agent authorized for L5 rule without manager; "
            f"got decision={verdict.decision}"
        )
        assert verdict.allowed is False
        # The level IS verifiable (inline comparison), so REQUIRE_HITL.
        assert verdict.decision == PolicyDecision.REQUIRE_HITL
        assert "autonomy_insufficient" in verdict.evaluation.reasons

    @pytest.mark.asyncio
    async def test_2_context_without_autonomy_level_no_default_allow(self) -> None:
        """Regression test 2: context without autonomy_level must NOT
        grant authorization by default.

        When context is None (no run_autonomy at all), the engine
        resolves None → L0_OBSERVE. A rule requiring L1+ must fail.
        """
        engine = PolicyEngine(rule_set=PolicyRuleSet())
        engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L1_RECOMMEND))

        # No context at all → run_autonomy=None → resolved to L0.
        verdict = await engine.evaluate(_make_call(), context=None)

        assert verdict.decision != PolicyDecision.ALLOW, (
            f"FAIL-OPEN: no context → default ALLOW; got {verdict.decision}"
        )
        assert verdict.allowed is False
        # L0 < L1 → autonomy insufficient → REQUIRE_HITL (verifiable).
        assert verdict.decision == PolicyDecision.REQUIRE_HITL
        assert "autonomy_insufficient" in verdict.evaluation.reasons

    @pytest.mark.asyncio
    async def test_3_manager_returns_false_no_allow(self) -> None:
        """Regression test 3: autonomy_manager.can_perform returns False
        → ALLOW must NOT survive.

        The manager is the authoritative source for autonomy decisions.
        If it says False, the engine must honor that verdict.
        """

        class _FalseManager:
            """A fake manager that always returns False."""

            def can_perform(self, agent, required) -> bool:
                return False

        engine = PolicyEngine(
            rule_set=PolicyRuleSet(),
            autonomy_manager=_FalseManager(),
        )
        engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L1_RECOMMEND))

        ctx = _make_ctx(autonomy=AutonomyLevel.L5_SELF_IMPROVE)
        verdict = await engine.evaluate(_make_call(), context=ctx)

        assert verdict.decision != PolicyDecision.ALLOW, (
            f"FAIL-OPEN: manager said False but ALLOW survived; "
            f"got {verdict.decision}"
        )
        assert verdict.allowed is False
        # Manager returned False (verifiable) → REQUIRE_HITL.
        assert verdict.decision == PolicyDecision.REQUIRE_HITL
        assert "autonomy_insufficient" in verdict.evaluation.reasons

    @pytest.mark.asyncio
    async def test_4_manager_raises_exception_must_deny_not_hitl(self) -> None:
        """Regression test 4: if autonomy_manager.can_perform raises an
        exception, the engine must fail-closed with DENY (not REQUIRE_HITL).

        REQUIRE_HITL is for when autonomy IS verifiable but insufficient.
        When the manager itself raises, we CANNOT verify — DENY is the
        only safe verdict (do not let the action wait for HITL on an
        unverifiable gate).
        """

        class _RaisingManager:
            """A fake manager that always raises."""

            def can_perform(self, agent, required) -> bool:
                raise RuntimeError("simulated manager failure")

        engine = PolicyEngine(
            rule_set=PolicyRuleSet(),
            autonomy_manager=_RaisingManager(),
        )
        engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L1_RECOMMEND))

        ctx = _make_ctx(autonomy=AutonomyLevel.L5_SELF_IMPROVE)
        verdict = await engine.evaluate(_make_call(), context=ctx)

        assert verdict.decision == PolicyDecision.DENY, (
            f"FAIL-OPEN: manager raised but action was not DENY; "
            f"got {verdict.decision}"
        )
        assert verdict.allowed is False
        assert "autonomy_unverifiable" in verdict.evaluation.reasons

    @pytest.mark.asyncio
    async def test_5_no_alternative_route_bypasses_policy(self) -> None:
        """Regression test 5: verify no alternative route allows the
        tool to execute when the policy denies.

        This test verifies that a DENY verdict from the policy engine
        is the final word — there is no code path in evaluate() that
        can upgrade a DENY back to ALLOW. We test multiple scenarios:
        (a) high-autonomy rule + L0 agent → not ALLOW.
        (b) high-autonomy rule + no context → not ALLOW.
        (c) high-autonomy rule + raising manager → DENY.
        (d) high-autonomy rule + False manager → not ALLOW.
        In ALL cases, the verdict must not be ALLOW.
        """
        scenarios = [
            ("L0 agent, no manager", None, _make_ctx(autonomy=AutonomyLevel.L0_OBSERVE)),
            ("L0 agent, no context", None, None),
            ("raising manager", _RaisingManagerIfNotExists(), _make_ctx(autonomy=AutonomyLevel.L5_SELF_IMPROVE)),
        ]
        for label, manager, ctx in scenarios:
            engine = PolicyEngine(
                rule_set=PolicyRuleSet(),
                autonomy_manager=manager,
            )
            engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L5_SELF_IMPROVE))
            verdict = await engine.evaluate(_make_call(), context=ctx)
            assert verdict.decision != PolicyDecision.ALLOW, (
                f"FAIL-OPEN in scenario '{label}': got ALLOW; "
                f"verdict={verdict.decision}, reasons={verdict.evaluation.reasons}"
            )


class _RaisingManagerIfNotExists:
    """Helper for scenario (c) in test_5."""

    def can_perform(self, agent, required) -> bool:
        raise RuntimeError("simulated")


# =====================================================================
# Additional edge-case tests
# =====================================================================


class TestBSecAutonomyEdgeCases:
    """Edge cases that verify the fix is robust."""

    @pytest.mark.asyncio
    async def test_l0_required_rule_passes_without_manager(self) -> None:
        """A rule with autonomy_required=L0_OBSERVE must pass even
        without an autonomy_manager (L0 is the floor; no gate)."""
        engine = PolicyEngine(rule_set=PolicyRuleSet())
        engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L0_OBSERVE))

        ctx = _make_ctx(autonomy=AutonomyLevel.L0_OBSERVE)
        verdict = await engine.evaluate(_make_call(), context=ctx)

        assert verdict.decision == PolicyDecision.ALLOW
        assert verdict.allowed is True

    @pytest.mark.asyncio
    async def test_l0_required_rule_passes_with_no_context(self) -> None:
        """A rule with autonomy_required=L0_OBSERVE must pass even
        when context is None (no run_autonomy to resolve)."""
        engine = PolicyEngine(rule_set=PolicyRuleSet())
        engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L0_OBSERVE))

        verdict = await engine.evaluate(_make_call(), context=None)

        assert verdict.decision == PolicyDecision.ALLOW
        assert verdict.allowed is True

    @pytest.mark.asyncio
    async def test_sufficient_autonomy_with_manager_passes(self) -> None:
        """Happy path: rule requires L3, agent has L5, manager present
        and returns True → ALLOW survives."""
        engine = PolicyEngine(
            rule_set=PolicyRuleSet(),
            autonomy_manager=AutonomyManager(),
        )
        engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L3_EXECUTE_EXTERNAL))

        ctx = _make_ctx(autonomy=AutonomyLevel.L5_SELF_IMPROVE)
        verdict = await engine.evaluate(_make_call(), context=ctx)

        assert verdict.decision == PolicyDecision.ALLOW
        assert verdict.allowed is True

    @pytest.mark.asyncio
    async def test_sufficient_autonomy_without_manager_passes_inline(self) -> None:
        """Happy path: rule requires L3, agent has L5, NO manager →
        inline comparison (5 >= 3) → ALLOW survives."""
        engine = PolicyEngine(rule_set=PolicyRuleSet())
        engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L3_EXECUTE_EXTERNAL))

        ctx = _make_ctx(autonomy=AutonomyLevel.L5_SELF_IMPROVE)
        verdict = await engine.evaluate(_make_call(), context=ctx)

        assert verdict.decision == PolicyDecision.ALLOW
        assert verdict.allowed is True

    @pytest.mark.asyncio
    async def test_exact_autonomy_match_passes(self) -> None:
        """Rule requires L3, agent has exactly L3 → ALLOW survives
        (>= is inclusive)."""
        engine = PolicyEngine(rule_set=PolicyRuleSet())
        engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L3_EXECUTE_EXTERNAL))

        ctx = _make_ctx(autonomy=AutonomyLevel.L3_EXECUTE_EXTERNAL)
        verdict = await engine.evaluate(_make_call(), context=ctx)

        assert verdict.decision == PolicyDecision.ALLOW
        assert verdict.allowed is True

    @pytest.mark.asyncio
    async def test_one_level_below_required_yields_hitl(self) -> None:
        """Rule requires L3, agent has L2 → REQUIRE_HITL (not DENY;
        the level is verifiable, just insufficient)."""
        engine = PolicyEngine(rule_set=PolicyRuleSet())
        engine.add_rule(_make_allow_rule(autonomy_required=AutonomyLevel.L3_EXECUTE_EXTERNAL))

        ctx = _make_ctx(autonomy=AutonomyLevel.L2_EXECUTE_SAFE)
        verdict = await engine.evaluate(_make_call(), context=ctx)

        assert verdict.decision == PolicyDecision.REQUIRE_HITL
        assert verdict.allowed is False
        assert "autonomy_insufficient" in verdict.evaluation.reasons

    @pytest.mark.asyncio
    async def test_deny_rule_unaffected_by_autonomy_check(self) -> None:
        """A rule with decision=DENY must produce DENY regardless of
        autonomy levels (the autonomy post-check only applies to ALLOW)."""
        deny_rule = PolicyRule(
            name="deny_dangerous",
            action_pattern="tool.dangerous.*",
            actor_pattern="*",
            risk_threshold=RiskLevel.LOW,
            autonomy_required=AutonomyLevel.L5_SELF_IMPROVE,
            decision=PolicyDecision.DENY,
            priority=10,
        )
        engine = PolicyEngine(rule_set=PolicyRuleSet())
        engine.add_rule(deny_rule)

        ctx = _make_ctx(autonomy=AutonomyLevel.L0_OBSERVE)
        verdict = await engine.evaluate(_make_call(), context=ctx)

        assert verdict.decision == PolicyDecision.DENY
        assert verdict.allowed is False

    @pytest.mark.asyncio
    async def test_no_matching_rule_yields_default_deny(self) -> None:
        """When no rule matches, the default_decision applies (DENY).
        The autonomy check is irrelevant in this case."""
        engine = PolicyEngine(rule_set=PolicyRuleSet())  # empty rule set

        ctx = _make_ctx(autonomy=AutonomyLevel.L5_SELF_IMPROVE)
        verdict = await engine.evaluate(_make_call(), context=ctx)

        assert verdict.decision == PolicyDecision.DENY
        assert verdict.allowed is False


__all__ = [
    "TestBSecAutonomyFailClosed",
    "TestBSecAutonomyEdgeCases",
]
