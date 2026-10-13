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

import asyncio
import hashlib
import json
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
    """In-memory HITL approval engine for use within a single asyncio event loop.

State and notifications are process-local; this class does not provide
thread-safety, cross-process coordination, or durable execution resumption.
"""

    def __init__(
        self,
        *,
        evidence_store: ApprovalEvidenceStore | None = None,
        escalation_policy: EscalationPolicy | None = None,
        default_deadline_seconds: float = 3600.0,
    ) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        # In-process notification primitives for coordinators waiting on a
        # human decision. These do not make execution state durable.
        self._decision_events: dict[str, asyncio.Event] = {}
        # Process-local at-most-once claim; not durable or cross-worker.
        self._claimed_tool_calls: set[tuple[str, str]] = set()
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
        # Bind a ToolCall approval to the exact call identity and a
        # canonical digest of its arguments. A future resume coordinator can
        # compare this trusted binding before dispatch; the digest is not an
        # authorization token and does not replace policy revalidation.
        arguments_sha256 = self._arguments_digest(arguments)
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
                "tool_call_id": action.id if isinstance(action, ToolCall) else None,
                "arguments_sha256": arguments_sha256,
                "arguments": arguments,
            },
            deadline=deadline or (utc_now() + timedelta(seconds=self._default_deadline)),
            scope=scope,
            evidence_ids=list(evidence_ids or []),
        )
        self._requests[req.approval_id] = req
        self._decision_events[req.approval_id] = asyncio.Event()
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
        """Approve a pending request, expiring it if the deadline has passed."""
        req = self._get_pending(approval_id)
        if req is None:
            return ApprovalResult(
                approval_id=approval_id,
                status=self._status_of(approval_id),
                reason="not_pending_or_missing",
            )
        # Enforce the deadline at decision time, not only during sweep_stale.
        # A delayed background sweep must never permit a late approval.
        if req.is_expired():
            req.mark_expired()
            req.reason = "deadline_passed"
            self._record_evidence(req)
            self._signal_decision(approval_id)
            log.info("approvals.expire_before_approve", approval_id=approval_id)
            return ApprovalResult(
                approval_id=approval_id,
                status=ApprovalStatus.EXPIRED,
                reason="deadline_passed",
            )
        req.mark_approved(by=approver)
        self._record_evidence(req, decided_by=approver)
        self._signal_decision(approval_id)
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
        """Reject a pending request, expiring it if the deadline has passed."""
        req = self._get_pending(approval_id)
        if req is None:
            return ApprovalResult(
                approval_id=approval_id,
                status=self._status_of(approval_id),
                reason="not_pending_or_missing",
            )
        # Rejection is also terminally recorded as EXPIRED when submitted late;
        # do not mutate a request after its deadline.
        if req.is_expired():
            req.mark_expired()
            req.reason = "deadline_passed"
            self._record_evidence(req)
            self._signal_decision(approval_id)
            log.info("approvals.expire_before_reject", approval_id=approval_id)
            return ApprovalResult(
                approval_id=approval_id,
                status=ApprovalStatus.EXPIRED,
                reason="deadline_passed",
            )
        req.mark_rejected(by=approver, reason=reason)
        self._record_evidence(req, decided_by=approver)
        self._signal_decision(approval_id)
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
        deadline_passed = req.is_expired()
        req.mark_expired()
        if deadline_passed:
            req.reason = "deadline_passed"
        self._record_evidence(req)
        self._signal_decision(approval_id)
        log.info("approvals.expire", approval_id=approval_id)
        return ApprovalResult(
            approval_id=approval_id,
            status=ApprovalStatus.EXPIRED,
            reason=req.reason or ("deadline_passed" if deadline_passed else ""),
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
            self._signal_decision(approval_id)
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
        self._signal_decision(approval_id)

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
        self._decision_events[child.approval_id] = asyncio.Event()
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

    async def wait_for_decision(self, approval_id: str) -> ApprovalResult:
        """Wait until a pending request is decided or its deadline expires.

        This is an in-process primitive for a future execution coordinator;
        it does not itself suspend/resume Agent.run() or provide durable
        cross-process notification.
        """
        req = self._requests.get(approval_id)
        if req is None:
            return ApprovalResult(
                approval_id=approval_id,
                status=ApprovalStatus.EXPIRED,
                reason="not_pending_or_missing",
            )
        if req.status != ApprovalStatus.PENDING:
            return ApprovalResult(
                approval_id=approval_id,
                status=req.status,
                decided_by=req.decided_by,
                reason=req.reason or "already_terminal",
            )

        event = self._decision_events.setdefault(approval_id, asyncio.Event())
        try:
            if req.deadline is None:
                await event.wait()
            else:
                remaining = (req.deadline - utc_now()).total_seconds()
                if remaining <= 0:
                    return await self.expire(approval_id)
                await asyncio.wait_for(event.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            # A concurrent decision may have won at the deadline boundary.
            current = self._requests.get(approval_id)
            if current is not None and current.status == ApprovalStatus.PENDING:
                return await self.expire(approval_id)

        current = self._requests.get(approval_id)
        if current is None:
            return ApprovalResult(
                approval_id=approval_id,
                status=ApprovalStatus.EXPIRED,
                reason="not_pending_or_missing",
            )
        if current.status == ApprovalStatus.PENDING and current.is_expired():
            return await self.expire(approval_id)
        return ApprovalResult(
            approval_id=approval_id,
            status=current.status,
            decided_by=current.decided_by,
            reason=current.reason,
        )

    def _signal_decision(self, approval_id: str) -> None:
        """Wake any in-process waiter after a terminal state transition."""
        event = self._decision_events.get(approval_id)
        if event is not None:
            event.set()

    @staticmethod
    def _arguments_digest(arguments: dict[str, Any]) -> str:
        """Return a stable digest for JSON-compatible tool arguments."""
        canonical_arguments = json.dumps(
            arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return hashlib.sha256(canonical_arguments.encode("utf-8")).hexdigest()

    def claim_approved_tool_call(
        self,
        approval_id: str,
        action: ToolCall,
        context: RunContext | None = None,
    ) -> bool:
        """Atomically claim an approved call once within this process.

        This synchronous check-and-set has no await point, so concurrent
        coroutines on the same event loop cannot both claim the same call.
        It is not durable and does not coordinate multiple workers.
        """
        req = self._requests.get(approval_id)
        if req is None or req.deadline is None or req.deadline <= utc_now():
            return False
        if not self.matches_approved_tool_call(approval_id, action, context):
            return False
        execution_id = (req.context_ref or {}).get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            return False
        claim_key = (execution_id, action.id)
        if claim_key in self._claimed_tool_calls:
            return False
        self._claimed_tool_calls.add(claim_key)
        return True

    def matches_approved_tool_call(
        self,
        approval_id: str,
        action: ToolCall,
        context: RunContext | None = None,
    ) -> bool:
        """Fail closed unless this exact tool call has an approved binding.

        This is a binding check only. Callers must still revalidate current
        policy and principal authorization immediately before dispatch.
        """
        req = self._requests.get(approval_id)
        if req is None or req.status != ApprovalStatus.APPROVED:
            return False
        binding = req.context_ref or {}
        try:
            digest = self._arguments_digest(action.arguments)
        except (TypeError, ValueError):
            return False
        return bool(
            req.action == action.tool_name
            and binding.get("tool_call_id") == action.id
            and binding.get("arguments_sha256") == digest
            and binding.get("execution_id") == (
                context.execution_id if context else None
            )
            and binding.get("correlation_id") == (
                context.correlation_id if context else None
            )
            and binding.get("agent_id") == (context.agent_id if context else None)
            and binding.get("session_id") == (context.session_id if context else None)
        )

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
