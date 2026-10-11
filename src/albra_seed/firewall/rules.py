"""FirewallRule model — pattern-matched rules for the ActionFirewall.

A FirewallRule declares:
  * action_pattern     — glob matching the action/tool name (e.g. "tool.http.*")
  * actor_pattern      — glob matching the actor identity (e.g. "agent:albra-*")
  * risk_threshold     — minimum RiskLevel at which the rule applies
  * autonomy_required  — minimum AutonomyLevel the actor must hold
  * decision           — ALLOW | DENY | REQUIRE_HITL | DRY_RUN
  * cooldown_seconds   — per-action cooldown to enforce after a match
  * rate_limit         — max actions per `rate_window_seconds`
  * rate_window_seconds— window for the rate limit
  * dry_run_capable    — whether the action can be executed in dry-run mode
  * priority           — higher priority rules evaluated first

DEFAULT-DENY: if NO rule matches, the firewall returns DENY.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import AutonomyLevel, PolicyDecision, RiskLevel, new_id, utc_now


class FirewallRule(BaseModel):
    """A single firewall rule."""

    rule_id: str = Field(default_factory=lambda: new_id("fw_rule"))
    name: str = ""
    description: str = ""
    action_pattern: str = "*"  # glob, e.g. "tool.http.*"
    actor_pattern: str = "*"  # glob, e.g. "agent:albra-*" or "*"
    risk_threshold: RiskLevel = RiskLevel.LOW  # rule applies only if risk >= threshold
    autonomy_required: AutonomyLevel = AutonomyLevel.L0_OBSERVE
    decision: PolicyDecision = PolicyDecision.DENY
    cooldown_seconds: int = 0  # 0 = no cooldown
    rate_limit: int = 0  # 0 = unlimited
    rate_window_seconds: int = 60
    dry_run_capable: bool = False
    priority: int = 0  # higher = evaluated first
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def matches_action(self, action_name: str) -> bool:
        import fnmatch
        return fnmatch.fnmatchcase(action_name, self.action_pattern)

    def matches_actor(self, actor_id: str) -> bool:
        import fnmatch
        return fnmatch.fnmatchcase(actor_id, self.actor_pattern)

    def applies_to(
        self,
        *,
        action_name: str,
        actor_id: str,
        risk: RiskLevel,
        autonomy: AutonomyLevel,
    ) -> bool:
        if not self.enabled:
            return False
        if not self.matches_action(action_name):
            return False
        if not self.matches_actor(actor_id):
            return False
        if _risk_rank(risk) < _risk_rank(self.risk_threshold):
            return False
        if autonomy < self.autonomy_required:
            return False
        return True


_RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.UNKNOWN: -1,
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _risk_rank(level: RiskLevel) -> int:
    return _RISK_RANK.get(level, -1)


__all__ = ["FirewallRule"]
