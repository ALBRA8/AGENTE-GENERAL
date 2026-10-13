"""Regression tests for approval deadlines and decision waiters."""

import asyncio
from datetime import timedelta

import pytest

from albra_seed.approvals import ApprovalEngine
from albra_seed.core.types import ApprovalStatus, utc_now


@pytest.mark.asyncio
async def test_late_approval_expires_instead_of_approving() -> None:
    engine = ApprovalEngine()
    pending = await engine.request("tool.external.http", deadline=utc_now() - timedelta(seconds=1))

    result = await engine.approve(pending.approval_id, approver="human:test")

    assert result.status == ApprovalStatus.EXPIRED
    assert result.reason == "deadline_passed"
    assert engine.get(pending.approval_id).status == ApprovalStatus.EXPIRED
    assert engine.verify_evidence_chain()


@pytest.mark.asyncio
async def test_late_rejection_expires_instead_of_mutating_request() -> None:
    engine = ApprovalEngine()
    pending = await engine.request("tool.external.http", deadline=utc_now() - timedelta(seconds=1))

    result = await engine.reject(
        pending.approval_id,
        approver="human:test",
        reason="too late",
    )

    assert result.status == ApprovalStatus.EXPIRED
    assert result.reason == "deadline_passed"
    assert engine.get(pending.approval_id).status == ApprovalStatus.EXPIRED
    assert engine.verify_evidence_chain()



@pytest.mark.asyncio
async def test_wait_for_decision_wakes_after_approval() -> None:
    engine = ApprovalEngine()
    pending = await engine.request(
        "tool.external.http",
        deadline=utc_now() + timedelta(seconds=2),
    )
    waiter = asyncio.create_task(engine.wait_for_decision(pending.approval_id))
    await asyncio.sleep(0)

    decided = await engine.approve(pending.approval_id, approver="human:test")
    observed = await asyncio.wait_for(waiter, timeout=1)

    assert decided.status == ApprovalStatus.APPROVED
    assert observed.status == ApprovalStatus.APPROVED
    assert observed.decided_by == "human:test"


@pytest.mark.asyncio
async def test_wait_for_decision_expires_at_deadline() -> None:
    engine = ApprovalEngine()
    pending = await engine.request(
        "tool.external.http",
        deadline=utc_now() + timedelta(milliseconds=20),
    )

    observed = await engine.wait_for_decision(pending.approval_id)

    assert observed.status == ApprovalStatus.EXPIRED
    assert observed.reason == "deadline_passed"
    assert engine.get(pending.approval_id).status == ApprovalStatus.EXPIRED
    assert engine.get(pending.approval_id).reason == "deadline_passed"
    assert engine.verify_evidence_chain()



@pytest.mark.asyncio
async def test_wait_for_decision_wakes_after_rejection() -> None:
    engine = ApprovalEngine()
    pending = await engine.request(
        "tool.external.http",
        deadline=utc_now() + timedelta(seconds=2),
    )
    waiter = asyncio.create_task(engine.wait_for_decision(pending.approval_id))
    await asyncio.sleep(0)

    rejected = await engine.reject(
        pending.approval_id,
        approver="human:test",
        reason="not authorized",
    )
    observed = await asyncio.wait_for(waiter, timeout=1)

    assert rejected.status == ApprovalStatus.REJECTED
    assert observed.status == ApprovalStatus.REJECTED
    assert observed.reason == "not authorized"


@pytest.mark.asyncio
async def test_terminal_approval_cannot_be_reversed_by_rejection() -> None:
    engine = ApprovalEngine()
    pending = await engine.request(
        "tool.external.http",
        deadline=utc_now() + timedelta(seconds=2),
    )

    approved = await engine.approve(pending.approval_id, approver="human:first")
    repeated = await engine.reject(
        pending.approval_id,
        approver="human:second",
        reason="conflicting decision",
    )

    assert approved.status == ApprovalStatus.APPROVED
    assert repeated.status == ApprovalStatus.APPROVED
    assert engine.get(pending.approval_id).status == ApprovalStatus.APPROVED
    assert engine.verify_evidence_chain()
