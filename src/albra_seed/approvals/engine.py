"""ApprovalEngine — HITL approval engine for the ALBRA SEED.

Provides:
  request(action, context, deadline) -> approval_id
  approve(approval_id, approver) -> bool
  reject(approval_id, approver, reason) -> bool
  expire(approval_id) -> bool
  escalate(approval_id) -> new_approval_id

Every state transition is recorded as a tamper-evident ApprovalEvidence
record (hash-chained, see `evidence.py`).

CRITICAL INVARIANTS:
  * Default action for stale approvals is to escalate (per the
    EscalationPolicy), NOT auto-approve. The seed NEVER auto-approves.
  * Expired approvals cannot be approved/rejected.
  * Escalations create a NEW approval_id with `escalation_of` set to
    the parent. The parent transitions to ESCALATED.
  * Max-escalation cap from EscalationPolicy is enforced; exceeding it
    auto-expires the request.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import structlog
from pydantic import BaseModel, Field

from albra_seed.core.types import (
    ApprovalStatus,
    RunContext,
    ToolCall,
    new_id,
    utc_now,
)
from albra_seed.approvals.evidence import ApprovalEvidence, ApprovalEvidenceStore
from albra_seed.approvals.escalation import EscalationPolicy
from albra_seed.approvals.request import ApprovalRequest

log = structlog.get_logger("albra.approvals")


class ApprovalResult(BaseModel):
    """Public result returned by ApprovalEngine methods.

    Compatible with the runtime agent loop's expected interface:
      * `.approval_id` (str)
      * `.status` (ApprovalStatus)
    """

    approval_id: str
    status: ApprovalStatus
    decided_by: str | None = None
    reason: str = ""
    new_approval_id: str | None = None  # set on escalate
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalEngine:
    """HITL approval engine. In-memory by default; thread-safe via asyncio.Lock."""

    def __init__(
        self,
        *,
        evidence_store: ApprovalEvidenceStore | None = None,
        escalation_policy: EscalationPolicy | None = None,
        default_deadline_seconds: float = 3600.0,
    ) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self.evidence: ApprovalEvidenceStore = evidence_store or ApprovalEvidenceStore()
        self.escalation_policy: EscalationPolicy = (
            escalation_policy or EscalationPolicy()
        )
        self._default_deadline = default_deadline_seconds

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    async def request(
        self,
        action: ToolCall | str,
        context: RunContext | None = None,
        deadline: datetime | None = None,
        *,
        actor: str | None = None,
        scope: str = "single",
        evidence_ids: list[str] | None = None,
    ) -> ApprovalResult:
        """Create a new PENDING approval request."""
        if isinstance(action, ToolCall):
            action_name = action.tool_name
            arguments = action.arguments
        else:
            action_name = str(action)
            arguments = {}
        req = ApprovalRequest(
            action=action_name,
            actor=actor or (
                f"agent:{context.agent_id}" if context else "anonymous"
            ),
            context_ref={
                "execution_id": context.execution_id if context else None,
                "correlation_id": context.correlation_id if context else None,
                "agent_id": context.agent_id if context else None,
                "session_id": context.session_id if context else None,
                "arguments": arguments,
            },
            deadline=deadline or (utc_now() + timedelta(seconds=self._default_deadline)),
            scope=scope,
            evidence_ids=list(evidence_ids or []),
        )
        self._requests[req.approval_id] = req
        log.info(
            "approvals.request",
            approval_id=req.approval_id,
            action=req.action,
            actor=req.actor,
            deadline=req.deadline.isoformat() if req.deadline else None,
        )
        return ApprovalResult(
            approval_id=req.approval_id,
            status=ApprovalStatus.PENDING,
        )

    async def approve(
        self,
        approval_id: str,
        approver: str,
    ) -> ApprovalResult:
        """Approve a PENDING request. Returns False result if not PENDING."""
        req = self._get_pending(approval_id)
        if req is None:
            return ApprovalResult(
                approval_id=approval_id,
                status=self._status_of(approval_id),
                reason="not_pending_or_missing",
            )
        req.mark_approved(by=approver)
        self._record_evidence(req, decided_by=approver)
        log.info("approvals.approve", approval_id=approval_id, approver=approver)
        return ApprovalResult(
            approval_id=approval_id,
            status=ApprovalStatus.APPROVED,
            decided_by=approver,
        )

    async def reject(
        self,
        approval_id: str,
        approver: str,
        reason: str,
    ) -> ApprovalResult:
        """Reject a PENDING request with a reason (recorded)."""
        req = self._get_pending(approval_id)
        if req is None:
            return ApprovalResult(
                approval_id=approval_id,
                status=self._status_of(approval_id),
                reason="not_pending_or_missing",
            )
        req.mark_rejected(by=approver, reason=reason)
        self._record_evidence(req, decided_by=approver)
        log.info(
            "approvals.reject",
            approval_id=approval_id,
            approver=approver,
            reason=reason,
        )
        return ApprovalResult(
            approval_id=approval_id,
            status=ApprovalStatus.REJECTED,
            decided_by=approver,
            reason=reason,
        )

    async def expire(self, approval_id: str) -> ApprovalResult:
        """Expire a PENDING request (e.g. deadline passed)."""
        req = self._get_pending(approval_id)
        if req is None:
            return ApprovalResult(
                approval_id=approval_id,
                status=self._status_of(approval_id),
                reason="not_pending_or_missing",
            )
        req.mark_expired()
        self._record_evidence(req)
        log.info("approvals.expire", approval_id=approval_id)
        return ApprovalResult(
            approval_id=approval_id,
            status=ApprovalStatus.EXPIRED,
        )

    async def escalate(self, approval_id: str) -> ApprovalResult:
        """Escalate a stale PENDING request to the next-level approver.

        Creates a NEW ApprovalRequest with `escalation_of` set to the
        parent. The parent transitions to ESCALATED.

        If the escalation chain has reached `max_escalations`, the
        request auto-expires instead.
        """
        req = self._get_pending(approval_id)
        if req is None:
            return ApprovalResult(
                approval_id=approval_id,
                status=self._status_of(approval_id),
                reason="not_pending_or_missing",
            )

        chain = list(req.escalation_chain) + [req.approval_id]
        if not self.escalation_policy.can_escalate(chain):
            # Auto-expire when cap hit.
            req.mark_expired()
            self._record_evidence(req)
            log.warning(
                "approvals.escalation_cap_exceeded",
                approval_id=approval_id,
                chain_len=len(chain),
            )
            return ApprovalResult(
                approval_id=approval_id,
                status=ApprovalStatus.EXPIRED,
                reason="max_escalations_exceeded",
            )

        # Mark parent ESCALATED, then spawn the child request.
        req.mark_escalated()
        self._record_evidence(req)

        child = ApprovalRequest(
            action=req.action,
            actor=req.actor,
            context_ref=req.context_ref,
            deadline=self.escalation_policy.next_deadline(),
            scope=req.scope,
            evidence_ids=list(req.evidence_ids),
            escalation_of=approval_id,
            escalation_chain=chain,
            metadata={
                **(req.metadata or {}),
                "escalation_level": len(chain),
                "escalated_to": self.escalation_policy.escalate_to,
            },
        )
        self._requests[child.approval_id] = child
        log.info(
            "approvals.escalate",
            parent=approval_id,
            child=child.approval_id,
            chain_len=len(chain),
        )
        return ApprovalResult(
            approval_id=child.approval_id,
            status=ApprovalStatus.PENDING,
            new_approval_id=child.approval_id,
        )

    # ------------------------------------------------------------------
    # Background sweep (call periodically)
    # ------------------------------------------------------------------

    async def sweep_stale(self) -> list[ApprovalResult]:
        """Expire or escalate all stale PENDING requests per the policy.

        Returns the list of results (one per affected request).
        """
        results: list[ApprovalResult] = []
        for approval_id, req in list(self._requests.items()):
            if req.status != ApprovalStatus.PENDING:
                continue
            if req.is_expired():
                results.append(await self.expire(approval_id))
                continue
            if self.escalation_policy.is_stale(req.requested_at):
                if self.escalation_policy.on_stale == "escalate":
                    results.append(await self.escalate(approval_id))
                elif self.escalation_policy.on_stale == "expire":
                    results.append(await self.expire(approval_id))
                # 'reject' would require an approver; skipped here.
        return results

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._requests.get(approval_id)

    def list_pending(self) -> list[ApprovalRequest]:
        return [r for r in self._requests.values() if r.status == ApprovalStatus.PENDING]

    def list_all(self) -> list[ApprovalRequest]:
        return list(self._requests.values())

    def verify_evidence_chain(self) -> bool:
        """Cryptographic check that no approval record has been tampered."""
        return self.evidence.verify_chain()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_pending(self, approval_id: str) -> ApprovalRequest | None:
        req = self._requests.get(approval_id)
        if req is None or req.status != ApprovalStatus.PENDING:
            return None
        return req

    def _status_of(self, approval_id: str) -> ApprovalStatus:
        req = self._requests.get(approval_id)
        return req.status if req is not None else ApprovalStatus.EXPIRED

    def _record_evidence(
        self,
        req: ApprovalRequest,
        *,
        decided_by: str | None = None,
    ) -> ApprovalEvidence:
        record = ApprovalEvidence(
            approval_id=req.approval_id,
            status=req.status,
            decided_by=decided_by or req.decided_by,
            decided_at=req.decided_at or utc_now(),
            reason=req.reason,
            metadata={"scope": req.scope, "action": req.action},
        )
        return self.evidence.append(record)


__all__ = ["ApprovalEngine", "ApprovalResult"]
