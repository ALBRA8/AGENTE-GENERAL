"""Unit tests for the firewall subpackage (F2-D).

Covers default-deny, rule matching, kill switch, cooldown manager,
and rate limiter.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from albra_seed.core.types import (
    AutonomyLevel,
    RiskLevel,
    RunContext,
    ToolCall,
)
from albra_seed.firewall import (
    ActionFirewall,
    CooldownManager,
    FirewallRule,
    KillSwitch,
    RateLimiter,
)


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# ACTION FIREWALL — DEFAULT DENY
# =====================================================================


def test_firewall_default_deny():
    """With no rules, every action MUST be DENIED."""
    fw = ActionFirewall()
    call = ToolCall(tool_name="tool.http.get", arguments={})
    decision = _run(fw.evaluate(call))
    assert decision.value == "deny"

    # String action name works too.
    decision2 = _run(fw.evaluate("tool.shell.exec"))
    assert decision2.value == "deny"

    # Verdict has correct metadata.
    verdict = _run(fw.verdict(call))
    assert verdict.allowed is False
    assert verdict.decision.value == "deny"
    assert "no_matching_rule" in verdict.reason


def test_firewall_allows_when_rule_matches():
    """A matching ALLOW rule overrides default-deny."""
    rule = FirewallRule(
        name="allow_safe_reads",
        action_pattern="tool.read.*",
        actor_pattern="*",
        risk_threshold=RiskLevel.LOW,
        autonomy_required=AutonomyLevel.L1_RECOMMEND,
        decision="allow",
        priority=10,
    )
    fw = ActionFirewall(rules=[rule])

    # Construct a context with sufficient autonomy + risk.
    ctx = RunContext(
        agent_id="agent-1",
        task="read",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        risk_level=RiskLevel.LOW,
    )
    call = ToolCall(tool_name="tool.read.dataset", arguments={})
    decision = _run(fw.evaluate(call, context=ctx))
    assert decision.value == "allow"

    # Non-matching action still denied.
    other = ToolCall(tool_name="tool.write.dataset", arguments={})
    assert _run(fw.evaluate(other, context=ctx)).value == "deny"

    # Insufficient risk level (below threshold) → rule does not apply → deny.
    low_risk_ctx = RunContext(
        agent_id="agent-1",
        task="read",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        risk_level=RiskLevel.UNKNOWN,  # below LOW
    )
    # rule requires risk >= LOW; UNKNOWN is ranked below LOW so rule does not match.
    assert _run(fw.evaluate(call, context=low_risk_ctx)).value == "deny"


# =====================================================================
# KILL SWITCH — GLOBAL BLOCK
# =====================================================================


def test_kill_switch_blocks_all_when_active():
    """When the kill switch is active, ALL actions are blocked."""
    ks = KillSwitch(cooldown_after_deactivate_seconds=0.0)
    assert ks.is_active() is False
    assert ks.block("tool.read") is False

    # Activate and verify all actions are blocked.
    _run(ks.activate("emergency: runaway spending", activated_by="ops"))
    assert ks.is_active() is True
    assert ks.block("tool.read.dataset") is True
    assert ks.block("tool.shell.exec") is True
    assert ks.block("tool.write.anything") is True
    assert ks.block(None) is True  # blocks even unknown actions

    # The firewall with kill switch must deny even ALLOW-rule matches.
    rule = FirewallRule(
        action_pattern="*",
        decision="allow",
        priority=10,
        risk_threshold=RiskLevel.LOW,
    )
    fw = ActionFirewall(rules=[rule], kill_switch=ks)
    ctx = RunContext(
        agent_id="a", task="t",
        autonomy_level=AutonomyLevel.L5_SELF_IMPROVE,
        risk_level=RiskLevel.LOW,
    )
    call = ToolCall(tool_name="anything", arguments={})
    verdict = _run(fw.verdict(call, context=ctx))
    assert verdict.decision.value == "deny"
    assert verdict.kill_switch_active is True

    # Deactivate restores normal operation.
    _run(ks.deactivate(by="ops"))
    assert ks.is_active() is False
    assert ks.block("tool.read") is False

    # Now the ALLOW rule fires again.
    verdict2 = _run(fw.verdict(call, context=ctx))
    assert verdict2.decision.value == "allow"


def test_kill_switch_audit_recorded():
    """Kill switch activation/deactivation produces audit records."""
    records: list[dict] = []

    async def sink(rec):
        records.append(rec)

    ks = KillSwitch(audit_sink=sink, cooldown_after_deactivate_seconds=0.0)
    _run(ks.activate("reason-1", activated_by="alice"))
    _run(ks.deactivate(by="bob"))
    events = [r.get("event") for r in records]
    assert "kill_switch.activated" in events
    assert "kill_switch.deactivated" in events
    # Verify activation metadata.
    act_rec = next(r for r in records if r["event"] == "kill_switch.activated")
    assert act_rec["reason"] == "reason-1"
    assert act_rec["activated_by"] == "alice"


# =====================================================================
# COOLDOWN MANAGER
# =====================================================================


def test_cooldown_manager():
    """CooldownManager triggers, reports cooling, and clears correctly."""
    cm = CooldownManager(default_duration_seconds=10.0)

    # Initially no cooldown.
    assert cm.is_cooling_down("tool.x") is False
    assert cm.remaining("tool.x") == 0

    # Trigger a cooldown.
    rec = cm.trigger("tool.x", seconds=100.0, reason="dangerous")
    assert rec.action == "tool.x"
    assert rec.duration_seconds == 100.0
    assert cm.is_cooling_down("tool.x") is True
    assert cm.remaining("tool.x") > 0
    assert cm.remaining("tool.x") <= 100

    # Different action not affected.
    assert cm.is_cooling_down("tool.y") is False

    # Clear removes the cooldown.
    assert cm.clear("tool.x") is True
    assert cm.is_cooling_down("tool.x") is False
    assert cm.clear("tool.x") is False  # already cleared

    # Trigger with default duration.
    cm.trigger("tool.z")
    assert cm.is_cooling_down("tool.z") is True
    assert cm.remaining("tool.z") <= 10

    # clear_all wipes everything.
    cm.trigger("tool.a")
    cm.trigger("tool.b")
    n = cm.clear_all()
    assert n >= 2
    assert cm.is_cooling_down("tool.a") is False
    assert cm.is_cooling_down("tool.b") is False

    # Expired cooldown auto-GC'd on next check.
    cm.trigger("tool.expired", seconds=0.0)
    # Sleep a tick so the cooldown expires.
    time.sleep(0.05)
    assert cm.is_cooling_down("tool.expired") is False
    assert cm.remaining("tool.expired") == 0


# =====================================================================
# RATE LIMITER — TOKEN BUCKET
# =====================================================================


def test_rate_limiter():
    """RateLimiter throttles when tokens run out and refills over time."""
    # Capacity 3, refill 1 token/sec.
    rl = RateLimiter(capacity=3, refill_per_sec=1.0)

    # First 3 calls consume all tokens.
    assert rl.try_consume("alice", "tool.x") is True
    assert rl.try_consume("alice", "tool.x") is True
    assert rl.try_consume("alice", "tool.x") is True
    # 4th call must be throttled.
    assert rl.try_consume("alice", "tool.x") is False

    # Different actor unaffected.
    assert rl.try_consume("bob", "tool.x") is True

    # Different action for alice unaffected (separate bucket).
    assert rl.try_consume("alice", "tool.y") is True

    # Wait for refill.
    time.sleep(1.05)
    # Alice should have ~1 token back.
    assert rl.try_consume("alice", "tool.x") is True
    assert rl.try_consume("alice", "tool.x") is False

    # reset removes the bucket.
    rl.reset("alice", "tool.x")
    # Fresh bucket starts at capacity.
    assert rl.try_consume("alice", "tool.x") is True
    assert rl.try_consume("alice", "tool.x") is True
    assert rl.try_consume("alice", "tool.x") is True

    # Invalid construction.
    with pytest.raises(ValueError):
        RateLimiter(capacity=0, refill_per_sec=1.0)
    with pytest.raises(ValueError):
        RateLimiter(capacity=1, refill_per_sec=0)

    # tokens_available does not consume.
    avail = rl.tokens_available("charlie", "tool.z")
    assert avail == 3.0
    # Still 3 — no consume happened.
    assert rl.tokens_available("charlie", "tool.z") == 3.0


def test_firewall_rate_limit_denies_when_throttled():
    """Firewall returns DENY with rate_limited=True when bucket empty."""
    rl = RateLimiter(capacity=1, refill_per_sec=0.001)  # very slow refill
    rule = FirewallRule(
        action_pattern="*",
        decision="allow",
        priority=10,
        risk_threshold=RiskLevel.LOW,
    )
    fw = ActionFirewall(rules=[rule], rate_limiter=rl)
    ctx = RunContext(
        agent_id="a", task="t",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        risk_level=RiskLevel.LOW,
    )
    call = ToolCall(tool_name="tool.read.x", arguments={})
    # First call consumes the only token — allowed.
    assert _run(fw.evaluate(call, context=ctx)).value == "allow"
    # Second call throttled — deny.
    v = _run(fw.verdict(call, context=ctx))
    assert v.decision.value == "deny"
    assert v.rate_limited is True


def test_firewall_cooldown_rule_triggers_cooldown():
    """An ALLOW rule with cooldown_seconds triggers a cooldown after match."""
    cm = CooldownManager()
    rule = FirewallRule(
        action_pattern="tool.dangerous.*",
        decision="allow",
        cooldown_seconds=60.0,
        priority=10,
        risk_threshold=RiskLevel.LOW,
    )
    fw = ActionFirewall(rules=[rule], cooldown=cm)
    ctx = RunContext(
        agent_id="a", task="t",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        risk_level=RiskLevel.LOW,
    )
    call = ToolCall(tool_name="tool.dangerous.run", arguments={})
    # First call allowed + triggers cooldown.
    assert _run(fw.evaluate(call, context=ctx)).value == "allow"
    assert cm.is_cooling_down("tool.dangerous.run") is True
    # Second call blocked by cooldown.
    v = _run(fw.verdict(call, context=ctx))
    assert v.decision.value == "deny"
    assert v.cooldown_remaining_seconds > 0
