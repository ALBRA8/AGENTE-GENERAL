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


class A2ASecurityDecision(BaseModel):
    """Outcome of an A2A security evaluation."""

    allowed: bool = False
    reason: str = ""
    policy_decision: PolicyDecision | None = None
    approval_id: str | None = None
    principal: AuthPrincipal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# Canonical action name used for firewall / policy evaluations.
A2A_RECEIVE_ACTION = "a2a.task.receive"


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
        """Run the full security pipeline on an inbound A2A task."""
        # ---- 1. AUTHENTICATE ---------------------------------------
        principal = await self._authenticate(token)
        if principal is None:
            return A2ASecurityDecision(
                allowed=False,
                reason="authentication_failed",
                principal=None,
            )

        # ---- 2. AUTHORIZE (no separate RBAC step yet; the firewall +
        # policy engine compose authorization). The principal's roles
        # are surfaced to the policy engine below. -------------------

        # Build the RunContext ONCE so both firewall and policy engine
        # see the same risk / autonomy classification.
        ctx = self._build_run_context(task, principal)

        # ---- 3. FIREWALL (lazy) ------------------------------------
        firewall = self._runtime.get_component(COMPONENT_FIREWALL)
        if firewall is not None:
            try:
                verdict = await firewall.verdict(
                    A2A_RECEIVE_ACTION,
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

        # ---- 4. POLICY (lazy) --------------------------------------
        policy_engine = self._runtime.get_component(COMPONENT_POLICY_ENGINE)
        if policy_engine is not None:
            try:
                verdict = await policy_engine.evaluate(
                    A2A_RECEIVE_ACTION,
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
                # Request HITL approval via the approval engine (if any).
                approval_id = await self._request_approval(task, ctx)
                return A2ASecurityDecision(
                    allowed=False,
                    reason="require_hitl",
                    policy_decision=decision,
                    approval_id=approval_id,
                    principal=principal,
                )
            # ALLOW / DRY_RUN / UNKNOWN → proceed.

        # ---- 5. AUTONOMY (lazy, via policy_engine.autonomy_manager) -
        # The autonomy check is already composed inside the policy
        # engine. If the policy engine is absent, we cannot check
        # autonomy here either (CORE_ONLY mode). No extra step needed.

        # ---- 6. ALL CHECKS PASSED ----------------------------------
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


__all__ = ["A2ASecurityFilter", "A2ASecurityDecision", "A2A_RECEIVE_ACTION"]
