"""ActionFirewall — the single gate every external action MUST pass through.

DEFAULT-DENY. If no rule explicitly matches an action, the firewall
returns PolicyDecision.DENY.

Evaluation order:
  1. Kill switch: if ACTIVE, immediately DENY.
  2. Cooldown: if action is cooling down, DENY.
  3. Rate limit: if no tokens available for (actor, action), DENY.
  4. Rules: first matching rule (by priority) determines the decision.
  5. No match: DENY.

Methods:
  evaluate(action, actor, context) -> PolicyDecision
  check(action, context)            -> FirewallVerdict (alias of evaluate
                                       for the runtime agent loop)

The runtime agent loop expects `firewall.check(call, context=RunContext)
-> object with .allowed: bool`. We provide both: `evaluate()` returns
the PolicyDecision; `check()` returns a `FirewallVerdict` with `.allowed`
and `.decision`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from pydantic import BaseModel, Field

from albra_seed.core.types import (
    AutonomyLevel,
    PolicyDecision,
    RiskLevel,
    RunContext,
    ToolCall,
    new_id,
    utc_now,
)
from albra_seed.firewall.cooldown import CooldownManager
from albra_seed.firewall.kill_switch import KillSwitch
from albra_seed.firewall.rate_limit import RateLimiter
from albra_seed.firewall.rules import FirewallRule, _risk_rank

log = structlog.get_logger("albra.firewall")


class FirewallVerdict(BaseModel):
    """Result of a firewall evaluation.

    `allowed` is True ONLY when decision == ALLOW. For REQUIRE_HITL and
    DRY_RUN, `allowed` is False — the caller MUST handle those explicitly.
    """

    verdict_id: str = Field(default_factory=lambda: new_id("fwv"))
    decision: PolicyDecision = PolicyDecision.DENY
    allowed: bool = False
    reason: str = ""
    matched_rule_id: str | None = None
    cooldown_remaining_seconds: int = 0
    rate_limited: bool = False
    kill_switch_active: bool = False
    evaluated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_decision(
        cls,
        decision: PolicyDecision,
        *,
        reason: str = "",
        matched_rule_id: str | None = None,
        **kwargs: Any,
    ) -> FirewallVerdict:
        return cls(
            decision=decision,
            allowed=(decision == PolicyDecision.ALLOW),
            reason=reason,
            matched_rule_id=matched_rule_id,
            **kwargs,
        )


class ActionFirewall:
    """The single action gate. DEFAULT-DENY.

    Constructed with:
      * rules          — list of FirewallRule (ordered by priority desc).
      * kill_switch    — global KillSwitch instance.
      * cooldown       — CooldownManager instance.
      * rate_limiter   — RateLimiter instance.

    All are optional with sensible defaults for CORE_ONLY mode.
    """

    def __init__(
        self,
        *,
        rules: list[FirewallRule] | None = None,
        kill_switch: KillSwitch | None = None,
        cooldown: CooldownManager | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._rules: list[FirewallRule] = sorted(
            rules or [], key=lambda r: r.priority, reverse=True
        )
        self.kill_switch: KillSwitch = kill_switch or KillSwitch()
        self.cooldown: CooldownManager = cooldown or CooldownManager()
        self.rate_limiter: RateLimiter | None = rate_limiter

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def add_rule(self, rule: FirewallRule) -> None:
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def remove_rule(self, rule_id: str) -> bool:
        for i, r in enumerate(self._rules):
            if r.rule_id == rule_id:
                del self._rules[i]
                return True
        return False

    def list_rules(self) -> list[FirewallRule]:
        return list(self._rules)

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        action: ToolCall | str,
        actor: Any = None,
        context: RunContext | None = None,
    ) -> PolicyDecision:
        """Evaluate an action and return a PolicyDecision."""
        verdict = await self.verdict(action, actor=actor, context=context)
        return verdict.decision

    async def verdict(
        self,
        action: ToolCall | str,
        actor: Any = None,
        context: RunContext | None = None,
    ) -> FirewallVerdict:
        """Full evaluation returning a FirewallVerdict."""
        action_name = action.tool_name if isinstance(action, ToolCall) else str(action)
        actor_id = _actor_id(actor, context)
        risk = _risk_of(context, action)
        autonomy = _autonomy_of(context)

        # 1. Kill switch — global block.
        if self.kill_switch.is_active():
            return FirewallVerdict.from_decision(
                PolicyDecision.DENY,
                reason="kill_switch_active",
                kill_switch_active=True,
            )

        # 2. Cooldown — per-action block.
        if self.cooldown.is_cooling_down(action_name):
            remaining = self.cooldown.remaining(action_name)
            return FirewallVerdict.from_decision(
                PolicyDecision.DENY,
                reason=f"action cooling down ({remaining}s remaining)",
                cooldown_remaining_seconds=remaining,
            )

        # 3. Rate limit — token bucket.
        if self.rate_limiter is not None:
            if not self.rate_limiter.try_consume(actor_id, action_name):
                return FirewallVerdict.from_decision(
                    PolicyDecision.DENY,
                    reason="rate_limit_exceeded",
                    rate_limited=True,
                )

        # 4. Rules — first match wins (priority order).
        for rule in self._rules:
            if not rule.applies_to(
                action_name=action_name,
                actor_id=actor_id,
                risk=risk,
                autonomy=autonomy,
            ):
                continue
            # Side-effects on ALLOW: trigger cooldown if rule mandates it.
            if rule.decision == PolicyDecision.ALLOW and rule.cooldown_seconds > 0:
                self.cooldown.trigger(
                    action_name,
                    seconds=rule.cooldown_seconds,
                    reason=f"firewall_rule:{rule.rule_id}",
                )
            return FirewallVerdict.from_decision(
                rule.decision,
                reason=f"rule:{rule.name or rule.rule_id}",
                matched_rule_id=rule.rule_id,
            )

        # 5. No match — DEFAULT-DENY.
        return FirewallVerdict.from_decision(
            PolicyDecision.DENY,
            reason="no_matching_rule",
        )

    async def check(
        self,
        call: ToolCall | str,
        context: RunContext | None = None,
    ) -> FirewallVerdict:
        """Alias used by the runtime agent loop (matches the contract in
        F2-A-runtime-builder.md): returns an object with `.allowed: bool`."""
        return await self.verdict(call, actor=None, context=context)


# =====================================================================
# Helpers
# =====================================================================


def _actor_id(actor: Any, context: RunContext | None) -> str:
    if actor is not None:
        if isinstance(actor, str):
            return actor
        # AuthPrincipal from security.auth
        identity = getattr(actor, "identity", None)
        if isinstance(identity, str):
            return identity
        if hasattr(actor, "principal_id"):
            return str(getattr(actor, "principal_id"))
    if context is not None:
        return f"agent:{context.agent_id}"
    return "anonymous"


def _risk_of(context: RunContext | None, action: Any) -> RiskLevel:
    if context is not None and context.risk_level != RiskLevel.UNKNOWN:
        return context.risk_level
    return RiskLevel.UNKNOWN


def _autonomy_of(context: RunContext | None) -> AutonomyLevel:
    if context is not None:
        return context.autonomy_level
    return AutonomyLevel.L0_OBSERVE


__all__ = ["ActionFirewall", "FirewallVerdict"]
