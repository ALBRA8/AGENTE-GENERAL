"""PolicyEvaluator — evaluates a single action context against the rule set.

The evaluator does NOT own the rules — it receives a `PolicyRuleSet`
plus an "action context" (a small pydantic model describing the action
to evaluate) and returns a `PolicyEvaluation` (decision + matched rule
+ reasons).

This separation lets us:
  * swap rule sets per-tenant / per-domain without re-instantiating,
  * unit-test the evaluator with synthetic contexts,
  * keep the PolicyEngine thin (it composes Evaluator + RuleSet + the
    permission + autonomy checks).
"""

from __future__ import annotations

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
from albra_seed.policy.rules import PolicyRule, PolicyRuleSet


class ActionContext(BaseModel):
    """A normalized context for policy evaluation.

    Designed to be constructed from a RunContext + a ToolCall by the
    PolicyEngine; kept separate so the evaluator has no RunContext dep.
    """

    action_name: str
    actor_id: str
    risk: RiskLevel = RiskLevel.UNKNOWN
    autonomy: AutonomyLevel = AutonomyLevel.L0_OBSERVE
    has_permission: bool = True  # caller pre-checks permission
    is_external: bool = False
    is_dry_run_capable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyEvaluation(BaseModel):
    """Result of a single policy evaluation."""

    evaluation_id: str = Field(default_factory=lambda: new_id("poleval"))
    decision: PolicyDecision = PolicyDecision.DENY
    matched_rule_id: str | None = None
    reasons: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=utc_now)
    context: ActionContext | None = None

    def allow(self) -> "PolicyEvaluation":
        self.decision = PolicyDecision.ALLOW
        return self

    def deny(self, reason: str) -> "PolicyEvaluation":
        self.decision = PolicyDecision.DENY
        if reason and reason not in self.reasons:
            self.reasons.append(reason)
        return self

    def require_hitl(self, reason: str) -> "PolicyEvaluation":
        self.decision = PolicyDecision.REQUIRE_HITL
        if reason and reason not in self.reasons:
            self.reasons.append(reason)
        return self

    def dry_run(self, reason: str) -> "PolicyEvaluation":
        self.decision = PolicyDecision.DRY_RUN
        if reason and reason not in self.reasons:
            self.reasons.append(reason)
        return self


class PolicyEvaluator:
    """Evaluates an ActionContext against a PolicyRuleSet.

    Algorithm:
      1. Find first matching rule (priority desc). If none, DEFAULT-DENY.
      2. If rule requires `required_permission` and `has_permission`
         is False → downgrade ALLOW → DENY, REQUIRE_HITL stays.
      3. If action is external AND not is_dry_run_capable AND decision
         is DRY_RUN → upgrade to REQUIRE_HITL.
      4. Return PolicyEvaluation.
    """

    def __init__(self, rule_set: PolicyRuleSet | None = None) -> None:
        self.rule_set: PolicyRuleSet = rule_set or PolicyRuleSet()

    def evaluate(self, ctx: ActionContext) -> PolicyEvaluation:
        rule = self.rule_set.first_match(
            action_name=ctx.action_name,
            actor_id=ctx.actor_id,
            risk=ctx.risk,
        )
        if rule is None:
            return PolicyEvaluation(
                decision=PolicyDecision.DENY,
                reasons=["no_matching_policy_rule"],
                context=ctx,
            )

        eval_ = PolicyEvaluation(
            decision=rule.decision,
            matched_rule_id=rule.rule_id,
            context=ctx,
        )

        # Permission gate: required permission must be present.
        if rule.required_permission and not ctx.has_permission:
            if rule.decision == PolicyDecision.ALLOW:
                eval_.decision = PolicyDecision.DENY
                eval_.reasons.append(
                    f"missing_permission:{rule.required_permission}"
                )
            else:
                eval_.reasons.append(
                    f"missing_permission:{rule.required_permission}"
                )

        # Dry-run capability gate: external actions without dry-run support
        # cannot be DRY_RUN — upgrade to REQUIRE_HITL.
        if (
            eval_.decision == PolicyDecision.DRY_RUN
            and ctx.is_external
            and not ctx.is_dry_run_capable
        ):
            eval_.decision = PolicyDecision.REQUIRE_HITL
            eval_.reasons.append("external_action_not_dry_run_capable")

        return eval_


__all__ = ["ActionContext", "PolicyEvaluation", "PolicyEvaluator"]
