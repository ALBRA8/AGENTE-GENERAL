"""Regression tests for approval decisions arriving after their deadline."""

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
