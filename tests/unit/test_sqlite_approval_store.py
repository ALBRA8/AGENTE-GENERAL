"""Concurrency, restart, and fail-closed tests for SQLite approval dispatch."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from albra_seed.approvals.request import ApprovalRequest
from albra_seed.approvals.sqlite_store import SQLiteApprovalStore
from albra_seed.core.types import ApprovalStatus


def _store(tmp_path: Path) -> SQLiteApprovalStore:
    return SQLiteApprovalStore(tmp_path / "approvals.sqlite")


def _approved(store: SQLiteApprovalStore, call_id: str = "call-1") -> str:
    req = store.create(ApprovalRequest(action="send_message"), call_id=call_id)
    assert store.transition(req.approval_id, ApprovalStatus.APPROVED, decided_by="reviewer")
    return req.approval_id


def test_approval_survives_new_store_instance(tmp_path):
    store = _store(tmp_path)
    req = store.create(ApprovalRequest(action="tool.write"), call_id="call-42")
    assert store.transition(req.approval_id, ApprovalStatus.APPROVED, decided_by="alice")
    reopened = _store(tmp_path)
    got = reopened.get(req.approval_id)
    assert got is not None
    assert got.status == ApprovalStatus.APPROVED
    assert got.context_ref["call_id"] == "call-42"
    assert got.decided_by == "alice"


def test_only_one_concurrent_decision_wins(tmp_path):
    store = _store(tmp_path)
    req = store.create(ApprovalRequest(action="external"))
    def decide(status):
        return _store(tmp_path).transition(req.approval_id, status, decided_by=status.value)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(decide, [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED] * 6))
    assert sum(results) == 1
    assert _store(tmp_path).get(req.approval_id).status in {
        ApprovalStatus.APPROVED, ApprovalStatus.REJECTED
    }


def test_two_workers_cannot_claim_same_call(tmp_path):
    store = _store(tmp_path)
    approval_id = _approved(store)
    assert store.prepare_dispatch(approval_id, "call-1")
    def claim(i):
        return _store(tmp_path).claim_dispatch("call-1", f"worker-{i}")
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(claim, range(16)))
    assert sum(results) == 1
    assert _store(tmp_path).dispatch("call-1")["state"] == "claimed"


def test_dispatch_requires_approved_linked_call(tmp_path):
    store = _store(tmp_path)
    req = store.create(ApprovalRequest(action="external"), call_id="call-1")
    assert not store.prepare_dispatch(req.approval_id, "call-1")
    assert store.transition(req.approval_id, ApprovalStatus.APPROVED)
    try:
        store.prepare_dispatch(req.approval_id, "call-other")
    except ValueError:
        pass
    else:
        raise AssertionError("must reject a call id different from the approved call")
    assert store.prepare_dispatch(req.approval_id, "call-1")


def test_duplicate_dispatch_slot_is_rejected(tmp_path):
    store = _store(tmp_path)
    approval_id = _approved(store)
    assert store.prepare_dispatch(approval_id, "call-1")
    assert not store.prepare_dispatch(approval_id, "call-1")


def test_restart_marks_inflight_dispatch_uncertain_not_retryable(tmp_path):
    store = _store(tmp_path)
    approval_id = _approved(store)
    assert store.prepare_dispatch(approval_id, "call-1")
    assert store.claim_dispatch("call-1", "worker-crashed")
    restarted = _store(tmp_path)
    assert restarted.recover_interrupted_dispatches() == 1
    assert restarted.dispatch("call-1")["state"] == "uncertain"
    assert not restarted.claim_dispatch("call-1", "worker-retry")


def test_claim_token_required_to_complete(tmp_path):
    store = _store(tmp_path)
    approval_id = _approved(store)
    assert store.prepare_dispatch(approval_id, "call-1")
    assert store.claim_dispatch("call-1", "worker-1")
    assert not store.complete_dispatch("call-1", "worker-2", {"ok": True})
    assert store.complete_dispatch("call-1", "worker-1", {"ok": True})
    assert not store.claim_dispatch("call-1", "worker-2")
    assert store.dispatch("call-1")["state"] == "completed"


def test_failed_external_attempt_is_never_automatically_requeued(tmp_path):
    store = _store(tmp_path)
    approval_id = _approved(store)
    assert store.prepare_dispatch(approval_id, "call-1")
    assert store.claim_dispatch("call-1", "worker-1")
    assert store.fail_dispatch("call-1", "worker-1", "provider timed out; outcome unknown")
    assert store.dispatch("call-1")["state"] == "uncertain"
    assert not store.claim_dispatch("call-1", "worker-2")
