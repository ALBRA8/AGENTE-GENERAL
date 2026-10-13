"""Approvals subpackage — HITL approval engine.

CRITICAL INVARIANTS:
  * The seed NEVER auto-approves. Stale requests escalate (per policy)
    or expire.
  * Every decision is recorded as tamper-evident ApprovalEvidence
    (hash-chained; tampering invalidates the chain).
  * Escalations create a NEW approval_id; the parent is marked ESCALATED.
  * Max-escalation cap from EscalationPolicy is enforced; exceeding it
    auto-expires the request.
  * Terminal states (APPROVED/REJECTED/EXPIRED) are immutable.
"""

from albra_seed.approvals.engine import ApprovalEngine, ApprovalResult
from albra_seed.approvals.evidence import ApprovalEvidence, ApprovalEvidenceStore
from albra_seed.approvals.escalation import EscalationPolicy
from albra_seed.approvals.request import ApprovalRequest
from albra_seed.approvals.sqlite_store import SQLiteSuspendedExecutionCoordinator
from albra_seed.approvals.suspension import (
    SuspendedExecution,
    SuspendedExecutionCoordinator,
    SuspendedExecutionError,
    SuspendedExecutionState,
)

__all__ = [
    "ApprovalEngine",
    "ApprovalResult",
    "ApprovalRequest",
    "ApprovalEvidence",
    "ApprovalEvidenceStore",
    "EscalationPolicy",
    "SuspendedExecution",
    "SuspendedExecutionCoordinator",
    "SuspendedExecutionError",
    "SuspendedExecutionState",
    "SQLiteSuspendedExecutionCoordinator",
]
