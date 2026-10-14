"""SQLite approval deadline enforcement regression tests."""
from __future__ import annotations

from datetime import timedelta

import pytest

from albra_seed.approvals.request import ApprovalRequest
from albra_seed.approvals.sqlite_store import SQLiteApprovalStore
from albra_seed.core.types import ApprovalStatus, utc_now


@pytest.mark.parametrize("target", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED])
def test_late_decision_expires_pending_approval_atomically(tmp_path, target):
    store = SQLiteApprovalStore(tmp_path / "approvals.sqlite")
    request = ApprovalRequest(
        action="tool.external.send",
        deadline=utc_now() - timedelta(seconds=1),
    )
    saved = store.create(request, call_id="call-late")

    assert not store.transition(
        saved.approval_id, target, decided_by="reviewer", reason="reviewed late"
    )

    restored = store.get(saved.approval_id)
    assert restored is not None
    assert restored.status == ApprovalStatus.EXPIRED
    assert restored.decided_by is None
    assert not store.prepare_dispatch(saved.approval_id, "call-late")


def test_expired_approval_cannot_be_dispatched_after_restart(tmp_path):
    path = tmp_path / "approvals.sqlite"
    store = SQLiteApprovalStore(path)
    request = ApprovalRequest(
        action="tool.external.send",
        deadline=utc_now() - timedelta(seconds=1),
    )
    saved = store.create(request, call_id="call-expired")

    # A late approval cannot create an authorized dispatch slot.
    assert not store.transition(saved.approval_id, ApprovalStatus.APPROVED)
    reopened = SQLiteApprovalStore(path)
    restored = reopened.get(saved.approval_id)
    assert restored is not None
    assert restored.status == ApprovalStatus.EXPIRED
    assert reopened.dispatch("call-expired") is None
