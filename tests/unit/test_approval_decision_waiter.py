"""Tests for waiting on a human approval decision within one process."""

from datetime import timedelta

import asyncio
import pytest

from albra_seed.approvals.engine import ApprovalEngine
from albra_seed.core.types import ApprovalStatus, utc_now


@pytest.mark.asyncio
async def test_wait_for_decision_wakes_after_approval() -> None:
    engine = ApprovalEngine()
    pending = await engine.request(
        "tool.external.send",
        deadline=utc_now() + timedelta(seconds=2),
    )

    waiter = asyncio.create_task(engine.wait_for_decision(pending.approval_id))
    await asyncio.sleep(0)
    decided = await engine.approve(pending.approval_id, approver="reviewer")
    observed = await asyncio.wait_for(waiter, timeout=1)

    assert decided.status == ApprovalStatus.APPROVED
    assert observed.status == ApprovalStatus.APPROVED
    assert observed.decided_by == "reviewer"


@pytest.mark.asyncio
async def test_wait_for_decision_wakes_after_rejection() -> None:
    engine = ApprovalEngine()
    pending = await engine.request(
        "tool.external.send",
        deadline=utc_now() + timedelta(seconds=2),
    )

    waiter = asyncio.create_task(engine.wait_for_decision(pending.approval_id))
    await asyncio.sleep(0)
    decided = await engine.reject(
        pending.approval_id, approver="reviewer", reason="not permitted"
    )
    observed = await asyncio.wait_for(waiter, timeout=1)

    assert decided.status == ApprovalStatus.REJECTED
    assert observed.status == ApprovalStatus.REJECTED
    assert observed.reason == "not permitted"


@pytest.mark.asyncio
async def test_wait_for_decision_expires_at_deadline() -> None:
    engine = ApprovalEngine()
    pending = await engine.request(
        "tool.external.send",
        deadline=utc_now() + timedelta(milliseconds=20),
    )

    observed = await asyncio.wait_for(
        engine.wait_for_decision(pending.approval_id), timeout=1
    )

    assert observed.status == ApprovalStatus.EXPIRED
    assert engine.get(pending.approval_id).status == ApprovalStatus.EXPIRED


@pytest.mark.asyncio
async def test_wait_for_decision_returns_existing_terminal_state() -> None:
    engine = ApprovalEngine()
    pending = await engine.request("tool.external.send")
    await engine.approve(pending.approval_id, approver="reviewer")

    observed = await engine.wait_for_decision(pending.approval_id)

    assert observed.status == ApprovalStatus.APPROVED
