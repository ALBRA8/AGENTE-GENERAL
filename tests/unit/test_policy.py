"""Unit tests for the policy subpackage (F2-D).

Covers PolicyEngine evaluation, REQUIRE_HITL for high-risk actions,
and first-match-wins ordering.
"""

from __future__ import annotations

import asyncio

import pytest

from albra_seed.core.types import (
    AutonomyLevel,
    PolicyDecision,
    RiskLevel,
    RunContext,
    ToolCall,
)
from albra_seed.policy import (
    ActionContext,
    PolicyEngine,
    PolicyEvaluator,
    PolicyRule,
    PolicyRuleSet,
)


def _run(coro):
    return asyncio.run(coro)


def test_policy_engine_evaluates_action():
    """PolicyEngine returns ALLOW when a matching ALLOW rule + sufficient
    autonomy are present."""
    rule = PolicyRule(
        name="allow_safe_reads",
        action_pattern="tool.read.*",
        actor_pattern="*",
        risk_threshold=RiskLevel.LOW,
        autonomy_required=AutonomyLevel.L1_RECOMMEND,
        decision=PolicyDecision.ALLOW,
        priority=10,
    )
    engine = PolicyEngine(rule_set=PolicyRuleSet())
    engine.add_rule(rule)

    ctx = RunContext(
        agent_id="a1", task="read",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        risk_level=RiskLevel.LOW,
    )
    call = ToolCall(tool_name="tool.read.dataset", arguments={})
    verdict = _run(engine.evaluate(call, context=ctx))
    assert verdict.decision == PolicyDecision.ALLOW
    assert verdict.allowed is True

    # Non-matching action → DENY (default-deny).
    other = ToolCall(tool_name="tool.write.dataset", arguments={})
    v2 = _run(engine.evaluate(other, context=ctx))
    assert v2.decision == PolicyDecision.DENY
    assert v2.allowed is False


def test_policy_returns_require_hitl_for_high_risk():
    """A REQUIRE_HITL rule for high-risk actions produces REQUIRE_HITL."""
    rule = PolicyRule(
        name="hitl_for_external_high_risk",
        action_pattern="tool.external.*",
        actor_pattern="*",
        risk_threshold=RiskLevel.HIGH,
        autonomy_required=AutonomyLevel.L3_EXECUTE_EXTERNAL,
        decision=PolicyDecision.REQUIRE_HITL,
        priority=20,
    )
    engine = PolicyEngine()
    engine.add_rule(rule)

    ctx = RunContext(
        agent_id="a1", task="external call",
        autonomy_level=AutonomyLevel.L3_EXECUTE_EXTERNAL,
        risk_level=RiskLevel.HIGH,
    )
    call = ToolCall(tool_name="tool.external.http", arguments={"url": "x"})
    verdict = _run(engine.evaluate(call, context=ctx))
    assert verdict.decision == PolicyDecision.REQUIRE_HITL
    assert verdict.allowed is False  # REQUIRE_HITL is NOT allow.

    # Verify the verdict carries the matched rule id + reasons.
    assert verdict.evaluation is not None
    assert verdict.evaluation.matched_rule_id == rule.rule_id


def test_policy_first_match_wins():
    """Higher-priority rules are evaluated first; first match wins."""
    high_prio_deny = PolicyRule(
        name="deny_critical_externals",
        action_pattern="tool.external.*",
        risk_threshold=RiskLevel.CRITICAL,
        decision=PolicyDecision.DENY,
        priority=100,
    )
    low_prio_allow = PolicyRule(
        name="allow_externals",
        action_pattern="tool.external.*",
        risk_threshold=RiskLevel.LOW,
        decision=PolicyDecision.ALLOW,
        priority=10,
    )
    engine = PolicyEngine()
    engine.add_rule(low_prio_allow)
    engine.add_rule(high_prio_deny)

    # Critical risk → high-priority DENY wins.
    ctx = RunContext(
        agent_id="a1", task="t",
        autonomy_level=AutonomyLevel.L5_SELF_IMPROVE,
        risk_level=RiskLevel.CRITICAL,
    )
    call = ToolCall(tool_name="tool.external.http", arguments={})
    v = _run(engine.evaluate(call, context=ctx))
    assert v.decision == PolicyDecision.DENY
    assert v.evaluation.matched_rule_id == high_prio_deny.rule_id

    # Low risk → high-prio rule doesn't apply (risk threshold not met);
    # lower-priority ALLOW applies.
    ctx_low = RunContext(
        agent_id="a1", task="t",
        autonomy_level=AutonomyLevel.L5_SELF_IMPROVE,
        risk_level=RiskLevel.LOW,
    )
    v2 = _run(engine.evaluate(call, context=ctx_low))
    assert v2.decision == PolicyDecision.ALLOW
    assert v2.evaluation.matched_rule_id == low_prio_allow.rule_id


def test_policy_allow_downgraded_to_deny_on_missing_permission():
    """An ALLOW rule with required_permission downgrades to DENY when
    the actor lacks the permission."""
    from albra_seed.security import (
        Permission,
        PermissionRegistry,
        Role,
        RoleRegistry,
    )

    roles = RoleRegistry()
    roles.register(Role(role_id="user", name="User"))
    perms = PermissionRegistry(roles=roles)
    perms.register(Permission(id="tool.execute.external"))
    perms.grant("user", "tool.execute.external")

    rule = PolicyRule(
        name="allow_externals_with_perm",
        action_pattern="tool.external.*",
        risk_threshold=RiskLevel.LOW,
        required_permission="tool.execute.external",
        decision=PolicyDecision.ALLOW,
        priority=10,
    )
    engine = PolicyEngine(permission_registry=perms)
    engine.add_rule(rule)

    ctx = RunContext(
        agent_id="a1", task="t",
        autonomy_level=AutonomyLevel.L5_SELF_IMPROVE,
        risk_level=RiskLevel.LOW,
    )
    call = ToolCall(tool_name="tool.external.http", arguments={})

    # Without providing actor_roles, no permission check applies (None registry
    # path) — but we DID pass the registry, so let's pass roles:
    # With the 'user' role granted, ALLOW survives.
    v_ok = _run(engine.evaluate(call, context=ctx, actor_roles=["user"]))
    assert v_ok.decision == PolicyDecision.ALLOW

    # With an empty roles list (no permission), ALLOW → DENY.
    v_deny = _run(engine.evaluate(call, context=ctx, actor_roles=[]))
    assert v_deny.decision == PolicyDecision.DENY
    assert any("missing_permission" in r for r in v_deny.evaluation.reasons)


def test_policy_evaluator_external_dry_run_upgrade():
    """PolicyEvaluator upgrades DRY_RUN → REQUIRE_HITL when external +
    not dry-run-capable."""
    rule = PolicyRule(
        action_pattern="*",
        risk_threshold=RiskLevel.LOW,
        decision=PolicyDecision.DRY_RUN,
        priority=10,
    )
    evaluator = PolicyEvaluator(PolicyRuleSet())
    evaluator.rule_set.add(rule)

    # External action not capable of dry-run → REQUIRE_HITL.
    ctx = ActionContext(
        action_name="tool.external.x",
        actor_id="alice",
        risk=RiskLevel.LOW,
        autonomy=AutonomyLevel.L2_EXECUTE_SAFE,
        has_permission=True,
        is_external=True,
        is_dry_run_capable=False,
    )
    eval_ = evaluator.evaluate(ctx)
    assert eval_.decision == PolicyDecision.REQUIRE_HITL
    assert "external_action_not_dry_run_capable" in eval_.reasons

    # Internal / dry-run-capable → DRY_RUN preserved.
    ctx2 = ctx.model_copy(update={"is_external": False})
    eval2 = evaluator.evaluate(ctx2)
    assert eval2.decision == PolicyDecision.DRY_RUN

    ctx3 = ctx.model_copy(update={"is_dry_run_capable": True})
    eval3 = evaluator.evaluate(ctx3)
    assert eval3.decision == PolicyDecision.DRY_RUN
