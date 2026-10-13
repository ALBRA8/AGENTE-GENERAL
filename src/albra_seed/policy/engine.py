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
from albra_seed.policy.evaluator import (
    ActionContext,
    PolicyEvaluation,
    PolicyEvaluator,
)
from albra_seed.policy.rules import PolicyRule, PolicyRuleSet

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

        # Pre-check permission (DEFAULT-DENY when permission is required).
        #
        # DEFAULT-DENY semantics (SEC-PERIMETER, Hallazgo 1):
        #   * If a matched rule has NO ``required_permission`` (i.e. it
        #     governs a public action), no permission gate applies and
        #     ``has_permission`` stays True. The rule's own ``decision``
        #     is the final verdict (ALLOW / DENY / REQUIRE_HITL).
        #   * If a matched rule REQUIRES a permission, the actor MUST
        #     prove they have it. ``has_permission`` is False until the
        #     permission registry explicitly grants it. If either:
        #       - ``self.permission_registry is None`` (no registry
        #         configured → cannot prove anything), OR
        #       - ``actor_roles is None`` (identity not verified by an
        #         Authenticator / IdentityResolver),
        #     the action is DENIED (fail-closed).
        #   * The registry call is wrapped in try/except so a broken
        #     registry (DB outage, etc.) NEVER opens the gate — it
        #     always closes it (``has_permission = False``).
        has_permission = True
        matched_rule = self.rule_set.first_match(
            action_name=action_name,
            actor_id=actor,
            risk=risk if risk is not None else RiskLevel.UNKNOWN,
        )
        if matched_rule is not None and matched_rule.required_permission:
            # DEFAULT-DENY for protected actions: starts False, only True
            # if the registry explicitly grants it.
            if self.permission_registry is None or actor_roles is None:
                # No registry or no verified roles → DENY (fail-closed).
                has_permission = False
            else:
                try:
                    has_permission = self.permission_registry.has_any(
                        actor_roles, matched_rule.required_permission
                    )
                except Exception:  # noqa: BLE001
                    has_permission = False  # fail-closed

        # Pre-check autonomy (FAIL-CLOSED).
        #
        # SECURITY INVARIANT (B-SEC-AUTONOMY):
        # An ALLOW rule with ``autonomy_required > L0_OBSERVE`` MUST NOT
        # pass without explicit verification. The previous code
        # initialized ``autonomy_ok = True`` and only ran the check
        # when ALL of (matched_rule, autonomy_manager, run_autonomy)
        # were present — a missing manager or missing context silently
        # bypassed the check, allowing a high-autonomy action to be
        # authorized without verification. That is fail-open.
        #
        # New semantics (fail-closed):
        #   * If ``matched_rule`` is None → autonomy check is
        #     irrelevant (``default_decision`` applies; no rule to
        #     enforce autonomy on). ``autonomy_ok`` stays False but is
        #     not consulted (the rule decision won't be ALLOW).
        #   * If ``matched_rule.autonomy_required == L0_OBSERVE`` →
        #     everyone passes (L0 is the floor; no autonomy gate).
        #   * Otherwise (rule requires L1+):
        #       - Resolve ``run_autonomy``: None → ``L0_OBSERVE``
        #         (most restrictive, per auditor instruction).
        #       - If ``autonomy_manager`` is available, call
        #         ``can_perform`` wrapped in try/except — fail-closed
        #         on exception.
        #       - If ``autonomy_manager`` is None, perform the inline
        #         comparison ``int(agent) >= int(required)`` (same
        #         semantics as ``AutonomyManager.can_perform``; the
        #         manager is a thin wrapper over this comparison, so
        #         omitting it does NOT weaken the check).
        #       - ``autonomy_ok`` is True ONLY if the comparison
        #         succeeds.
        #
        # Post-check distinction (DENY vs REQUIRE_HITL):
        #   * ALLOW + ``autonomy_ok=False`` + ``autonomy_verifiable=True``
        #     → ``REQUIRE_HITL`` (the level IS verifiable, just
        #     insufficient; a human can approve at a higher level).
        #   * ALLOW + ``autonomy_ok=False`` + ``autonomy_verifiable=False``
        #     → ``DENY`` (we could NOT verify — manager raised an
        #     exception; fail-closed; do not let the action wait for
        #     HITL on an unverifiable gate).
        autonomy_ok = False
        autonomy_verifiable = False
        if matched_rule is not None:
            required = matched_rule.autonomy_required
            if required == AutonomyLevel.L0_OBSERVE:
                # L0 is the floor — everyone passes, no gate.
                autonomy_ok = True
                autonomy_verifiable = True
            else:
                # Rule requires L1+. Resolve the agent's level.
                # None → L0_OBSERVE (most restrictive, per auditor).
                resolved_autonomy = (
                    run_autonomy
                    if run_autonomy is not None
                    else AutonomyLevel.L0_OBSERVE
                )
                if self.autonomy_manager is not None:
                    try:
                        autonomy_ok = bool(
                            self.autonomy_manager.can_perform(
                                resolved_autonomy, required
                            )
                        )
                        autonomy_verifiable = True
                    except Exception:  # noqa: BLE001
                        # Manager raised — cannot verify. Fail-closed.
                        autonomy_ok = False
                        autonomy_verifiable = False
                else:
                    # No manager configured. The autonomy check is a
                    # simple integer comparison (agent_level >=
                    # required_level). We perform it inline so the
                    # check is NEVER skipped. This is the same logic
                    # AutonomyManager.can_perform uses.
                    try:
                        autonomy_ok = (
                            int(resolved_autonomy) >= int(required)
                        )
                        autonomy_verifiable = True
                    except Exception:  # noqa: BLE001
                        autonomy_ok = False
                        autonomy_verifiable = False

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
        #
        # SECURITY INVARIANT (B-SEC-AUTONOMY):
        #   * ALLOW + ``not has_permission`` → DENY (permission is
        #     absolute; cannot be escalated by HITL).
        #   * ALLOW + ``not autonomy_ok`` + ``autonomy_verifiable`` →
        #     REQUIRE_HITL (the level IS verifiable, just insufficient;
        #     a human can approve at a higher level).
        #   * ALLOW + ``not autonomy_ok`` + ``not autonomy_verifiable``
        #     → DENY (we could NOT verify — manager raised an exception;
        #     fail-closed; do not let the action wait for HITL on an
        #     unverifiable gate).
        #
        # In all three cases, the action does NOT reach the executor
        # (both DENY and REQUIRE_HITL block execution). The distinction
        # is whether HITL can upgrade the verdict.
        if eval_.decision == PolicyDecision.ALLOW:
            if not has_permission:
                eval_.decision = PolicyDecision.DENY
                eval_.reasons.append("permission_missing")
            elif not autonomy_ok:
                if autonomy_verifiable:
                    eval_.decision = PolicyDecision.REQUIRE_HITL
                    eval_.reasons.append("autonomy_insufficient")
                else:
                    eval_.decision = PolicyDecision.DENY
                    eval_.reasons.append("autonomy_unverifiable")

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
