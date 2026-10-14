"""Cross-worker waiting and transition regressions for SQLite-backed approvals."""
import asyncio

import pytest

from albra_seed.approvals.engine import ApprovalEngine
from albra_seed.approvals.sqlite_store import SQLiteApprovalStore
from albra_seed.core.types import ApprovalStatus


@pytest.mark.asyncio
async def test_sqlite_waiter_observes_decision_from_another_engine(tmp_path):
    database = tmp_path / "approvals.sqlite3"
    waiting_engine = ApprovalEngine(store=SQLiteApprovalStore(database))
    deciding_engine = ApprovalEngine(store=SQLiteApprovalStore(database))

    request = await waiting_engine.request("external.send")
    approval_id = request.approval_id
    waiter = asyncio.create_task(waiting_engine.wait_for_decision(approval_id))

    # Give the waiter time to enter its durable polling loop. The second
    # engine has an independent in-memory cache and event registry.
    await asyncio.sleep(0.05)
    decision = await deciding_engine.approve(approval_id, approver="reviewer-1")
    assert decision.status is ApprovalStatus.APPROVED

    observed = await asyncio.wait_for(waiter, timeout=2)
    assert observed.status is ApprovalStatus.APPROVED
    assert observed.decided_by == "reviewer-1"


@pytest.mark.asyncio
async def test_sqlite_transition_refreshes_stale_worker_cache(tmp_path):
    database = tmp_path / "approvals.sqlite3"
    first = ApprovalEngine(store=SQLiteApprovalStore(database))
    second = ApprovalEngine(store=SQLiteApprovalStore(database))

    request = await first.request("external.send")
    approval_id = request.approval_id

    approved = await first.approve(approval_id, approver="reviewer-1")
    assert approved.status is ApprovalStatus.APPROVED

    # The second engine never loaded the request before the transition.
    # It must consult SQLite rather than treating its empty/stale cache as
    # the source of truth or creating a competing terminal decision.
    rejected = await second.reject(
        approval_id, approver="reviewer-2", reason="late competing decision"
    )
    assert rejected.status is ApprovalStatus.APPROVED
    assert rejected.reason == "not_pending_or_missing"
    persisted = second.get(approval_id)
    assert persisted is not None
    assert persisted.status is ApprovalStatus.APPROVED
    assert persisted.decided_by == "reviewer-1"
