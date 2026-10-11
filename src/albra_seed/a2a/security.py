"""A2A security filter — full enforcement pipeline.

The filter NEVER bypasses the existing security stack. It is a thin
orchestrator that walks an A2ATask through the standard ALBRA security
pipeline:

  AUTHENTICATE → AUTHORIZE → FIREWALL → POLICY → HITL(if required)
  → AUTONOMY

Each step is LAZY: if the runtime has no component registered for that
step, the step is SKIPPED (CORE_ONLY mode). This means:

  * NoopAuthenticator (the default) → anonymous principal → allowed.
  * Real Authenticator + bad token → denied.
  * No firewall registered → step skipped.
  * ActionFirewall with DEFAULT-DENY → unknown actions denied.
  * PolicyEngine returning REQUIRE_HITL → decision with approval_id;
    task is NOT executed until the approval is granted.

The filter produces an `A2ASecurityDecision` summarising the outcome.
It does NOT itself execute the task — that is the A2AServer's job.

Two public methods are provided:

  * ``evaluate(task, *, token)`` — full pipeline for the
    ``message/send`` path. Includes the HITL/approval flow (because the
    task carries enough context to request an approval).
  * ``evaluate_action(action, *, task_id=None, token=None, target=None)``
    — the same AUTHENTICATE → FIREWALL → POLICY pipeline for the
    ``tasks/get`` and ``tasks/cancel`` paths. These are read/state-mutating
    operations on a previously-submitted task; they do NOT request HITL
    approvals (a deny is a deny — there is no task body to approve).

Both methods share ``_evaluate_common`` so the security logic is defined
in exactly one place.

The token is NEVER logged, stored in metadata, or surfaced in evidence /
audit records. Only the resolved ``principal`` (its ``identity`` /
``principal_id``) reaches the audit log via ``record_audit``.
"""

from __future__ import annotations

import contextlib
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.a2a.contract import A2ATask
from albra_seed.core.types import (
    AutonomyLevel,
    PolicyDecision,
    RiskLevel,
    RunContext,
    new_id,
)
from albra_seed.runtime.runtime import (
    COMPONENT_APPROVAL_ENGINE,
    COMPONENT_FIREWALL,
    COMPONENT_POLICY_ENGINE,
)
from albra_seed.security.auth import AuthPrincipal, NoopAuthenticator


# Canonical action names used for firewall / policy evaluations.
A2A_RECEIVE_ACTION = "a2a.task.receive"
A2A_GET_STATUS_ACTION = "a2a.tasks.get"
A2A_CANCEL_ACTION = "a2a.tasks.cancel"


class A2ASecurityDecision(BaseModel):
    """Outcome of an A2A security evaluation."""

    allowed: bool = False
    reason: str = ""
    policy_decision: PolicyDecision | None = None
    approval_id: str | None = None
    principal: AuthPrincipal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2ASecurityFilter:
    """Full-stack security filter for inbound A2A tasks.

    The filter is constructed with the runtime so it can LAZILY resolve
    the firewall / policy engine / approval engine / authenticator. If
    any of these are absent (CORE_ONLY mode), the corresponding step is
    skipped — the filter NEVER raises for a missing component.
    """

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------
    async def evaluate(
        self,
        task: A2ATask,
        *,
        token: str | None = None,
    ) -> A2ASecurityDecision:
        """Run the full security pipeline on an inbound A2A ``message/send``.

        Adds the HITL/approval flow on top of ``_evaluate_common``: when
        the policy engine returns ``REQUIRE_HITL`` the filter tries to
        create an approval request via the (lazy) approval engine and
        surfaces the approval_id on the decision.
        """
        # 1. AUTHENTICATE
        principal = await self._authenticate(token)
        if principal is None:
            return A2ASecurityDecision(
                allowed=False,
                reason="authentication_failed",
                principal=None,
            )

        # 2. Build the RunContext ONCE so both firewall and policy engine
        # see the same risk / autonomy classification.
        ctx = self._build_run_context(task, principal)

        # 3. FIREWALL + POLICY (shared with evaluate_action).
        decision = await self._evaluate_common(
            action=A2A_RECEIVE_ACTION,
            principal=principal,
            ctx=ctx,
        )

        # 4. If the policy required HITL, request an approval using the
        # task body (the approval engine needs a requester / context).
        if (
            not decision.allowed
            and decision.reason == "require_hitl"
            and decision.policy_decision == PolicyDecision.REQUIRE_HITL
        ):
            approval_id = await self._request_approval(task, ctx)
            decision.approval_id = approval_id
        return decision

    async def evaluate_action(
        self,
        action: str,
        *,
        task_id: str | None = None,
        token: str | None = None,
        target: str | None = None,
    ) -> A2ASecurityDecision:
        """Run the security pipeline for a non-``message/send`` A2A operation.

        Used by ``A2AServer.get_status`` and ``A2AServer.cancel``. These
        operations do NOT carry a full task body — they reference a
        previously-submitted task by id. The filter therefore builds a
        minimal RunContext from ``(task_id, target)`` and runs the same
        AUTHENTICATE → FIREWALL → POLICY pipeline.

        HITL approvals are NOT requested for these operations: a
        ``REQUIRE_HITL`` verdict is treated as a deny (there is no task
        body to approve). Callers receive the decision and may audit the
        outcome (the reason string carries the verdict).
        """
        # 1. AUTHENTICATE
        principal = await self._authenticate(token)
        if principal is None:
            return A2ASecurityDecision(
                allowed=False,
                reason="authentication_failed",
                principal=None,
            )

        # 2. Minimal RunContext (no task body to inspect for risk/autonomy).
        ctx = self._build_action_context(
            task_id=task_id, target=target, principal=principal
        )

        # 3. FIREWALL + POLICY (shared with evaluate).
        return await self._evaluate_common(
            action=action,
            principal=principal,
            ctx=ctx,
        )

    # -----------------------------------------------------------------
    # Shared pipeline (firewall + policy). Both evaluate() and
    # evaluate_action() call this so the security logic exists once.
    # -----------------------------------------------------------------
    async def _evaluate_common(
        self,
        *,
        action: str,
        principal: AuthPrincipal,
        ctx: RunContext,
    ) -> A2ASecurityDecision:
        """AUTHENTICATE'd pipeline: FIREWALL → POLICY.

        Returns an ``A2ASecurityDecision`` with ``reason`` set to one of:
          * ``allowed``                  — all checks passed
          * ``firewall_error:<exc>``      — firewall raised
          * ``firewall_denied:<reason>``  — firewall verdict denied
          * ``policy_error:<exc>``        — policy engine raised
          * ``policy_denied``             — policy returned DENY
          * ``require_hitl``              — policy returned REQUIRE_HITL
                                           (caller decides whether to
                                           request an approval)

        The token is NEVER included in the decision or its metadata —
        only the resolved principal (``principal.identity`` /
        ``principal.principal_id``) is surfaced.
        """
        # ---- FIREWALL (lazy) ---------------------------------------
        firewall = self._runtime.get_component(COMPONENT_FIREWALL)
        if firewall is not None:
            try:
                verdict = await firewall.verdict(
                    action,
                    actor=principal,
                    context=ctx,
                )
            except Exception as exc:  # noqa: BLE001
                # Firewall failures default to DENY.
                return A2ASecurityDecision(
                    allowed=False,
                    reason=f"firewall_error:{exc}",
                    principal=principal,
                )
            if not verdict.allowed:
                # REQUIRE_HITL verdicts from the firewall are surfaced.
                pd = getattr(verdict, "decision", PolicyDecision.DENY)
                return A2ASecurityDecision(
                    allowed=False,
                    reason=f"firewall_denied:{verdict.reason}",
                    policy_decision=pd,
                    principal=principal,
                )

        # ---- POLICY (lazy) -----------------------------------------
        policy_engine = self._runtime.get_component(COMPONENT_POLICY_ENGINE)
        if policy_engine is not None:
            try:
                verdict = await policy_engine.evaluate(
                    action,
                    context=ctx,
                    actor_roles=list(principal.roles),
                    actor_id=principal.identity,
                )
            except Exception as exc:  # noqa: BLE001
                return A2ASecurityDecision(
                    allowed=False,
                    reason=f"policy_error:{exc}",
                    principal=principal,
                )
            decision = verdict.decision
            if decision == PolicyDecision.DENY:
                return A2ASecurityDecision(
                    allowed=False,
                    reason="policy_denied",
                    policy_decision=decision,
                    principal=principal,
                )
            if decision == PolicyDecision.REQUIRE_HITL:
                # NOTE: the caller (evaluate) may attach an approval_id
                # if it has a task body to submit for approval. For
                # evaluate_action (read/state-mutating ops on a stored
                # task) we surface REQUIRE_HITL as a deny — there is no
                # task body to approve.
                return A2ASecurityDecision(
                    allowed=False,
                    reason="require_hitl",
                    policy_decision=decision,
                    principal=principal,
                )
            # ALLOW / DRY_RUN / UNKNOWN → proceed.

        # ---- ALL CHECKS PASSED -------------------------------------
        return A2ASecurityDecision(
            allowed=True,
            reason="allowed",
            principal=principal,
        )

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------
    async def _authenticate(self, token: str | None) -> AuthPrincipal | None:
        """Resolve the principal via the runtime's authenticator.

        Default authenticator is NoopAuthenticator (anonymous principal
        returned for any token). When a real Authenticator is registered
        and `verify` returns None, the principal is None → denied.

        The token is passed ONLY to the authenticator's ``verify`` method.
        It is NEVER logged here, NEVER stored on the principal, and NEVER
        returned to the caller.
        """
        authenticator = self._runtime.get_component("authenticator")
        if authenticator is None:
            authenticator = NoopAuthenticator()
        try:
            return await authenticator.verify(token)
        except Exception:  # noqa: BLE001
            return None

    def _build_run_context(
        self, task: A2ATask, principal: AuthPrincipal
    ) -> RunContext:
        """Build a RunContext for the policy engine evaluation."""
        risk_value = task.metadata.get("risk") if task.metadata else None
        try:
            risk = RiskLevel(risk_value) if isinstance(risk_value, str) else RiskLevel.UNKNOWN
        except ValueError:
            risk = RiskLevel.UNKNOWN
        autonomy = AutonomyLevel.L0_OBSERVE
        if task.metadata and "autonomy_level" in task.metadata:
            with contextlib.suppress(ValueError, TypeError):
                autonomy = AutonomyLevel(int(task.metadata["autonomy_level"]))
        return RunContext(
            execution_id=task.task_id,
            correlation_id=task.correlation_id or new_id("corr"),
            parent_execution_id=task.parent_execution_id,
            agent_id=task.target,
            task=task.objective or "(a2a task)",
            autonomy_level=autonomy,
            risk_level=risk,
        )

    def _build_action_context(
        self,
        *,
        task_id: str | None,
        target: str | None,
        principal: AuthPrincipal,
    ) -> RunContext:
        """Build a minimal RunContext for a ``tasks/get`` / ``tasks/cancel``
        security check.

        There is no task body to inspect for risk / autonomy metadata, so
        we use ``RiskLevel.UNKNOWN`` and ``AutonomyLevel.L0_OBSERVE`` —
        the same defaults ``_build_run_context`` uses when a task has no
        metadata. The ``execution_id`` is set to the queried ``task_id``
        (or a fresh id if absent) so audit / provenance chains stay
        correlatable.
        """
        return RunContext(
            execution_id=task_id or new_id("exec"),
            correlation_id=new_id("corr"),
            agent_id=target or "a2a-server",
            task="(a2a action query)",
            autonomy_level=AutonomyLevel.L0_OBSERVE,
            risk_level=RiskLevel.UNKNOWN,
        )

    async def _request_approval(self, task: A2ATask, ctx: RunContext) -> str | None:
        """Create a PENDING approval request and return its id.

        Returns None when no approval engine is registered (the task is
        simply denied in that case — the caller treats None as 'no
        approval pending, deny').
        """
        approval_engine = self._runtime.get_component(COMPONENT_APPROVAL_ENGINE)
        if approval_engine is None:
            return None
        try:
            result = await approval_engine.request(
                A2A_RECEIVE_ACTION,
                context=ctx,
                actor=task.requester,
            )
            return getattr(result, "approval_id", None)
        except Exception:  # noqa: BLE001
            return None


__all__ = [
    "A2ASecurityFilter",
    "A2ASecurityDecision",
    "A2A_RECEIVE_ACTION",
    "A2A_GET_STATUS_ACTION",
    "A2A_CANCEL_ACTION",
]
