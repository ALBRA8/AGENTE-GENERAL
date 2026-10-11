"""Unit tests for the approvals subpackage (F2-D).

Covers approval request + approve, rejection with reason, TTL expiry,
and escalation chain.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from albra_seed.approvals import (
    ApprovalEngine,
    ApprovalEvidence,
    ApprovalEvidenceStore,
    ApprovalRequest,
    EscalationPolicy,
)
from albra_seed.core.types import ApprovalStatus, ToolCall, utc_now


def _run(coro):
    return asyncio.run(coro)


def _future(seconds: float = 3600.0) -> datetime:
    return utc_now() + timedelta(seconds=seconds)


def _past(seconds: float = 10.0) -> datetime:
    return utc_now() - timedelta(seconds=seconds)


def test_approval_request_and_approve():
    """ApprovalEngine.request creates a PENDING request; approve sets APPROVED."""
    eng = ApprovalEngine()
    call = ToolCall(tool_name="tool.external.http", arguments={"url": "x"})
    result = _run(eng.request(call, deadline=_future(seconds=3600)))
    assert result.approval_id is not None
    assert result.status == ApprovalStatus.PENDING

    # The request must be retrievable and pending.
    req = eng.get(result.approval_id)
    assert req is not None
    assert req.is_pending is True
    assert req.is_terminal is False
    assert req.action == "tool.external.http"
    assert req.scope == "single"

    # Approve it.
    approve_result = _run(eng.approve(result.approval_id, approver="alice"))
    assert approve_result.status == ApprovalStatus.APPROVED
    assert approve_result.decided_by == "alice"

    # The request is now APPROVED (terminal).
    req2 = eng.get(result.approval_id)
    assert req2.is_terminal is True
    assert req2.is_pending is False
    assert req2.decided_by == "alice"
    assert req2.decided_at is not None

    # Cannot approve an already-terminal request.
    again = _run(eng.approve(result.approval_id, approver="bob"))
    assert again.status == ApprovalStatus.APPROVED  # unchanged
    assert "not_pending_or_missing" in again.reason

    # Evidence was recorded.
    evs = eng.evidence.list_for(result.approval_id)
    assert len(evs) == 1
    assert evs[0].status == ApprovalStatus.APPROVED
    assert evs[0].decided_by == "alice"

    # Tamper-evidence chain is intact.
    assert eng.verify_evidence_chain() is True


def test_approval_rejection_records_reason():
    """ApprovalEngine.reject stores the reason and APPROVER."""
    eng = ApprovalEngine()
    call = ToolCall(tool_name="tool.write.db", arguments={})
    r = _run(eng.request(call))
    assert r.status == ApprovalStatus.PENDING

    reject = _run(eng.reject(r.approval_id, approver="bob", reason="violation of policy X"))
    assert reject.status == ApprovalStatus.REJECTED
    assert reject.decided_by == "bob"
    assert reject.reason == "violation of policy X"

    req = eng.get(r.approval_id)
    assert req.status == ApprovalStatus.REJECTED
    assert req.reason == "violation of policy X"
    assert req.decided_by == "bob"

    # Evidence captured the rejection.
    evs = eng.evidence.list_for(r.approval_id)
    assert len(evs) == 1
    assert evs[0].status == ApprovalStatus.REJECTED
    assert evs[0].reason == "violation of policy X"


def test_approval_expires_after_ttl():
    """An approval past its deadline can be expired via expire() or sweep_stale."""
    eng = ApprovalEngine()
    call = ToolCall(tool_name="tool.external.sensitive", arguments={})

    # Set a deadline in the past.
    past = _past(seconds=10)
    r = _run(eng.request(call, deadline=past))
    assert r.status == ApprovalStatus.PENDING

    # Explicit expire.
    exp = _run(eng.expire(r.approval_id))
    assert exp.status == ApprovalStatus.EXPIRED
    req = eng.get(r.approval_id)
    assert req.is_terminal is True
    assert req.status == ApprovalStatus.EXPIRED

    # Also test sweep_stale path.
    r2 = _run(eng.request(call, deadline=_past(seconds=5)))
    results = _run(eng.sweep_stale())
    assert any(res.approval_id == r2.approval_id for res in results)
    req2 = eng.get(r2.approval_id)
    assert req2.status == ApprovalStatus.EXPIRED


def test_approval_escalation():
    """Stale approvals escalate: parent → ESCALATED, new child PENDING."""
    policy = EscalationPolicy(
        stale_after_seconds=0.0,  # immediately stale
        escalate_to="supervisor",
        max_escalations=3,
        next_deadline_extension_seconds=3600.0,
    )
    eng = ApprovalEngine(escalation_policy=policy)
    call = ToolCall(tool_name="tool.high.value.transfer", arguments={})
    r = _run(eng.request(call))
    parent_id = r.approval_id

    # Escalate once — parent becomes ESCALATED, child is PENDING.
    esc1 = _run(eng.escalate(parent_id))
    assert esc1.status == ApprovalStatus.PENDING
    assert esc1.new_approval_id is not None
    child_id = esc1.new_approval_id
    assert child_id != parent_id

    parent = eng.get(parent_id)
    assert parent.status == ApprovalStatus.ESCALATED
    assert parent.is_pending is False  # no further direct state changes
    assert parent.decided_at is not None

    child = eng.get(child_id)
    assert child.is_pending is True
    assert child.escalation_of == parent_id
    assert parent_id in child.escalation_chain
    assert child.metadata.get("escalation_level") == 1
    assert child.metadata.get("escalated_to") == "supervisor"

    # Escalate the child too — chain grows.
    esc2 = _run(eng.escalate(child_id))
    grandchild_id = esc2.new_approval_id
    grandchild = eng.get(grandchild_id)
    assert grandchild.escalation_of == child_id
    assert child_id in grandchild.escalation_chain
    assert parent_id in grandchild.escalation_chain

    # Escalation evidence chain is intact.
    assert eng.verify_evidence_chain() is True

    # Hit max_escalations=3 — next escalation auto-expires.
    esc3 = _run(eng.escalate(grandchild_id))
    # grandchild → grandgrandchild (level 3 — still within cap)
    if esc3.new_approval_id:
        # Now try to escalate a 4th time — should auto-expire.
        esc4 = _run(eng.escalate(esc3.new_approval_id))
        assert esc4.status == ApprovalStatus.EXPIRED
        assert "max_escalations_exceeded" in esc4.reason


def test_approval_evidence_tamper_detection():
    """Tampering with an ApprovalEvidence record breaks the chain."""
    store = ApprovalEvidenceStore()
    # Append three records.
    for i in range(3):
        store.append(ApprovalEvidence(
            approval_id=f"appr_{i}",
            status=ApprovalStatus.APPROVED,
            decided_by=f"approver_{i}",
            reason=f"reason_{i}",
        ))
    assert store.verify_chain() is True

    # Tamper with the middle record's reason — chain must now break.
    records = store.all_records()
    assert len(records) == 3
    records[1].reason = "TAMPERED"
    # Note: record_hash was computed BEFORE the tamper — verify_chain
    # will detect the mismatch.
    assert store.verify_chain() is False


def test_approval_request_terminal_immutability():
    """Terminal requests cannot transition to any other state."""
    req = ApprovalRequest(action="tool.x", actor="alice")
    req.mark_approved(by="bob")
    with pytest.raises(ValueError):
        req.mark_rejected(by="other", reason="x")
    with pytest.raises(ValueError):
        req.mark_expired()
    with pytest.raises(ValueError):
        req.mark_escalated()
