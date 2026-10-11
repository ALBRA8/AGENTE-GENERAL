"""PolicyEngine — top-level policy decision engine.

Composes:
  * a PolicyEvaluator (rule-matching)
  * a PermissionRegistry (RBAC grants)
  * an AutonomyManager (autonomy-level checks)
  * a fallback decision when everything else is silent

Algorithm (per action):
  1. Build an ActionContext from the (call, context) tuple.
  2. Pre-check permission: does the actor's roles include the
     rule-required permission? (PermissionRegistry.has_any)
  3. Pre-check autonomy: is the agent's autonomy_level sufficient?
     (AutonomyManager.can_perform)
  4. Evaluate via the PolicyEvaluator (first-match-wins).
  5. If decision is ALLOW but autonomy insufficient → REQUIRE_HITL.
  6. If decision is ALLOW but permission missing → DENY.
  7. Return the final PolicyDecision.

DEFAULT-DENY: unknown actions return DENY.

The engine is async because the permission registry / autonomy
manager MAY need to consult external stores in the future. The
seed's in-memory implementations are synchronous underneath.
"""

from __future__ import annotations

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
from albra_seed.policy.evaluator import (
    ActionContext,
    PolicyEvaluation,
    PolicyEvaluator,
)
from albra_seed.policy.rules import PolicyRule, PolicyRuleSet
from datetime import datetime

log = structlog.get_logger("albra.policy")


class PolicyVerdict(BaseModel):
    """Public-facing verdict from PolicyEngine.evaluate.

    `decision` is the final PolicyDecision. `evaluation` is the
    underlying PolicyEvaluation (matched rule, reasons). Compatible
    with the runtime agent loop's expectation: has `.decision`.
    """

    verdict_id: str = Field(default_factory=lambda: new_id("polv"))
    decision: PolicyDecision = PolicyDecision.DENY
    allowed: bool = False
    evaluation: PolicyEvaluation | None = None
    evaluated_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def from_eval(cls, eval_: PolicyEvaluation) -> PolicyVerdict:
        return cls(
            decision=eval_.decision,
            allowed=(eval_.decision == PolicyDecision.ALLOW),
            evaluation=eval_,
        )


class PolicyEngine:
    """Top-level policy decision engine.

    The engine takes optional dependencies; missing ones degrade
    gracefully (autonomy check passes if no manager; permission check
    passes if no registry).
    """

    def __init__(
        self,
        *,
        rule_set: PolicyRuleSet | None = None,
        evaluator: PolicyEvaluator | None = None,
        permission_registry: Any = None,
        autonomy_manager: Any = None,
        default_decision: PolicyDecision = PolicyDecision.DENY,
    ) -> None:
        self.rule_set: PolicyRuleSet = rule_set or PolicyRuleSet()
        self.evaluator: PolicyEvaluator = evaluator or PolicyEvaluator(self.rule_set)
        self.permission_registry = permission_registry
        self.autonomy_manager = autonomy_manager
        self.default_decision = default_decision

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def add_rule(self, rule: PolicyRule) -> None:
        self.rule_set.add(rule)

    def remove_rule(self, rule_id: str) -> bool:
        return self.rule_set.remove(rule_id)

    def list_rules(self) -> list[PolicyRule]:
        return self.rule_set.list_rules()

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        call: ToolCall | str,
        context: RunContext | None = None,
        *,
        actor_roles: list[str] | None = None,
        actor_id: str | None = None,
    ) -> PolicyVerdict:
        """Evaluate a (call, context) and return a PolicyVerdict."""
        action_name = call.tool_name if isinstance(call, ToolCall) else str(call)
        run_autonomy = context.autonomy_level if context is not None else None
        risk = context.risk_level if context is not None else None
        actor = actor_id or (
            f"agent:{context.agent_id}" if context is not None else "anonymous"
        )

        # Pre-check permission (DEFAULT-DENY when permission is required
        # and missing). Without a registry, we treat as "no permission check"
        # — i.e. `has_permission=True` — and rely on rules for the deny.
        # If actor_roles is explicitly a list (even empty), we check it.
        # If actor_roles is None (not provided), no check is performed.
        has_permission = True
        matched_rule = self.rule_set.first_match(
            action_name=action_name,
            actor_id=actor,
            risk=risk if risk is not None else RiskLevel.UNKNOWN,
        )
        if (
            matched_rule is not None
            and matched_rule.required_permission
            and self.permission_registry is not None
            and actor_roles is not None
        ):
            has_permission = self.permission_registry.has_any(
                actor_roles, matched_rule.required_permission
            )

        # Pre-check autonomy (DEFAULT-DENY when required autonomy not met).
        autonomy_ok = True
        if (
            matched_rule is not None
            and self.autonomy_manager is not None
            and run_autonomy is not None
        ):
            autonomy_ok = self.autonomy_manager.can_perform(
                run_autonomy, matched_rule.autonomy_required
            )

        # Build ActionContext and evaluate.
        ctx = ActionContext(
            action_name=action_name,
            actor_id=actor,
            risk=risk if risk is not None else RiskLevel.UNKNOWN,
            autonomy=run_autonomy if run_autonomy is not None else AutonomyLevel.L0_OBSERVE,
            has_permission=has_permission,
            is_external=bool(getattr(matched_rule, "metadata", {}).get("is_external", False))
            if matched_rule
            else False,
            is_dry_run_capable=bool(
                getattr(matched_rule, "metadata", {}).get("is_dry_run_capable", False)
            )
            if matched_rule
            else False,
        )
        eval_ = self.evaluator.evaluate(ctx)

        # Post-checks: ALLOW requires both permission AND autonomy.
        if eval_.decision == PolicyDecision.ALLOW:
            if not has_permission:
                eval_.decision = PolicyDecision.DENY
                eval_.reasons.append("permission_missing")
            elif not autonomy_ok:
                eval_.decision = PolicyDecision.REQUIRE_HITL
                eval_.reasons.append("autonomy_insufficient")

        log.info(
            "policy.evaluate",
            action=action_name,
            actor=actor,
            decision=eval_.decision.value,
            matched_rule=eval_.matched_rule_id,
            reasons=eval_.reasons,
        )
        return PolicyVerdict.from_eval(eval_)


__all__ = ["PolicyEngine", "PolicyVerdict"]
