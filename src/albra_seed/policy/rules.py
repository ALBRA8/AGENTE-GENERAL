"""PolicyRule + PolicyRuleSet — ordered rules for the policy engine.

A PolicyRule declares a declarative predicate over an action context:

  * action_pattern   — glob matching action/tool name
  * actor_pattern    — glob matching actor identity
  * risk_threshold   — minimum RiskLevel at which the rule applies
  * autonomy_required— minimum AutonomyLevel required (NOT enforced here;
                       the AutonomyManager enforces this. Rule only
                       advertises the requirement for record-keeping.)
  * required_permission — permission id required (string); empty = no check
  * decision         — ALLOW | DENY | REQUIRE_HITL | DRY_RUN | UNKNOWN
  * priority         — higher priority rules evaluated first

`PolicyRuleSet` keeps rules sorted by priority desc. First match wins.
DEFAULT-DENY: if no rule matches, the engine returns DENY.

NO DOMAIN POLICIES live in the seed. Specialization layers register them.
"""

from __future__ import annotations

import fnmatch
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import (
    AutonomyLevel,
    PolicyDecision,
    RiskLevel,
    new_id,
    utc_now,
)


class PolicyRule(BaseModel):
    """A declarative policy rule. No domain knowledge."""

    rule_id: str = Field(default_factory=lambda: new_id("pol"))
    name: str = ""
    description: str = ""
    action_pattern: str = "*"
    actor_pattern: str = "*"
    risk_threshold: RiskLevel = RiskLevel.LOW
    autonomy_required: AutonomyLevel = AutonomyLevel.L0_OBSERVE
    required_permission: str = ""
    decision: PolicyDecision = PolicyDecision.DENY
    priority: int = 0
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def matches_action(self, action_name: str) -> bool:
        return fnmatch.fnmatchcase(action_name, self.action_pattern)

    def matches_actor(self, actor_id: str) -> bool:
        return fnmatch.fnmatchcase(actor_id, self.actor_pattern)

    def applies(
        self,
        *,
        action_name: str,
        actor_id: str,
        risk: RiskLevel,
    ) -> bool:
        if not self.enabled:
            return False
        if not self.matches_action(action_name):
            return False
        if not self.matches_actor(actor_id):
            return False
        if _risk_rank(risk) < _risk_rank(self.risk_threshold):
            return False
        return True


class PolicyRuleSet:
    """Ordered rule set. First match wins (priority desc, then insertion)."""

    def __init__(self) -> None:
        self._rules: list[PolicyRule] = []
        self._insertion_order: dict[str, int] = {}
        self._next_order = 0

    def add(self, rule: PolicyRule) -> None:
        if rule.rule_id not in self._insertion_order:
            self._insertion_order[rule.rule_id] = self._next_order
            self._next_order += 1
        self._rules.append(rule)
        # Stable sort: priority desc, then insertion order asc.
        self._rules = sorted(
            self._rules,
            key=lambda r: (-r.priority, self._insertion_order[r.rule_id]),
        )

    def remove(self, rule_id: str) -> bool:
        for i, r in enumerate(self._rules):
            if r.rule_id == rule_id:
                del self._rules[i]
                self._insertion_order.pop(rule_id, None)
                return True
        return False

    def list_rules(self) -> list[PolicyRule]:
        return list(self._rules)

    def first_match(
        self,
        *,
        action_name: str,
        actor_id: str,
        risk: RiskLevel,
    ) -> PolicyRule | None:
        for rule in self._rules:  # already priority-sorted desc
            if rule.applies(
                action_name=action_name,
                actor_id=actor_id,
                risk=risk,
            ):
                return rule
        return None


_RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.UNKNOWN: -1,
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _risk_rank(level: RiskLevel) -> int:
    return _RISK_RANK.get(level, -1)


__all__ = ["PolicyRule", "PolicyRuleSet"]
