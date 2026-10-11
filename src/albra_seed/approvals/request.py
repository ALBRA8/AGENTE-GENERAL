"""ApprovalRequest — model for a Human-In-The-Loop (HITL) approval.

A request is created by the ApprovalEngine when:
  * the policy engine returns REQUIRE_HITL, OR
  * the action's risk level exceeds the agent's autonomy, OR
  * an explicit escalation policy fires.

Fields mirror the task spec:
  id, action, actor, context, deadline, status, scope, evidence_ids,
  requested_at, decided_at, decided_by, reason.

State transitions:
  PENDING → APPROVED   (approve)
  PENDING → REJECTED    (reject, with reason)
  PENDING → EXPIRED     (expire)
  PENDING → ESCALATED   (escalate → spawns new request)
  APPROVED/REJECTED/EXPIRED are terminal.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import (
    ApprovalStatus,
    RunContext,
    ToolCall,
    new_id,
    utc_now,
)


class ApprovalRequest(BaseModel):
    """A single HITL approval request."""

    approval_id: str = Field(default_factory=lambda: new_id("appr"))
    action: str  # action/tool name
    actor: str = "anonymous"
    context_ref: dict[str, Any] = Field(default_factory=dict)
    deadline: datetime | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    scope: str = "single"  # single | session | workflow | global
    evidence_ids: list[str] = Field(default_factory=list)
    requested_at: datetime = Field(default_factory=utc_now)
    decided_at: datetime | None = None
    decided_by: str | None = None
    reason: str = ""
    escalation_of: str | None = None  # parent approval_id if escalated
    escalation_chain: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # -- state helpers --------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
        }

    @property
    def is_pending(self) -> bool:
        return self.status == ApprovalStatus.PENDING

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.deadline is None:
            return False
        now = now or utc_now()
        return now > self.deadline

    # -- mutators (called by ApprovalEngine) ----------------------------

    def mark_approved(self, *, by: str) -> None:
        if not self.is_pending:
            raise ValueError(f"cannot approve: status={self.status.value}")
        self.status = ApprovalStatus.APPROVED
        self.decided_at = utc_now()
        self.decided_by = by

    def mark_rejected(self, *, by: str, reason: str) -> None:
        if not self.is_pending:
            raise ValueError(f"cannot reject: status={self.status.value}")
        self.status = ApprovalStatus.REJECTED
        self.decided_at = utc_now()
        self.decided_by = by
        self.reason = reason

    def mark_expired(self) -> None:
        if not self.is_pending:
            raise ValueError(f"cannot expire: status={self.status.value}")
        self.status = ApprovalStatus.EXPIRED
        self.decided_at = utc_now()

    def mark_escalated(self) -> None:
        if not self.is_pending:
            raise ValueError(f"cannot escalate: status={self.status.value}")
        self.status = ApprovalStatus.ESCALATED
        self.decided_at = utc_now()

    @classmethod
    def from_call(
        cls,
        call: ToolCall,
        *,
        actor: str = "anonymous",
        context: RunContext | None = None,
        deadline_seconds: float = 3600.0,
        scope: str = "single",
    ) -> ApprovalRequest:
        return cls(
            action=call.tool_name,
            actor=actor,
            context_ref={
                "execution_id": context.execution_id if context else None,
                "correlation_id": context.correlation_id if context else None,
                "agent_id": context.agent_id if context else None,
                "session_id": context.session_id if context else None,
                "arguments": call.arguments,
            },
            deadline=utc_now() + timedelta(seconds=deadline_seconds),
            scope=scope,
        )


__all__ = ["ApprovalRequest"]
