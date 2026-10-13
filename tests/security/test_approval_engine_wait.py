"""Regression tests for waiting on explicit HITL approval decisions."""

import asyncio

from albra_seed.approvals.engine import ApprovalEngine
from albra_seed.core.types import ApprovalStatus, ToolCall


def test_wait_for_decision_resolves_only_after_explicit_approval() -> None:
    async def scenario() -> None:
        engine = ApprovalEngine(default_deadline_seconds=2)
        requested = await engine.request(
            ToolCall(tool_name="sensitive_action", arguments={"target": "x"})
        )
        assert requested.status == ApprovalStatus.PENDING

        waiter = asyncio.create_task(
            engine.wait_for_decision(requested.approval_id)
        )
        await asyncio.sleep(0)
        assert not waiter.done()

        approved = await engine.approve(requested.approval_id, "reviewer-1")
        assert approved.status == ApprovalStatus.APPROVED
        resolved = await waiter
        assert resolved.status == ApprovalStatus.APPROVED
        assert resolved.decided_by == "reviewer-1"

    asyncio.run(scenario())


def test_wait_for_decision_fails_closed_when_deadline_expires() -> None:
    async def scenario() -> None:
        engine = ApprovalEngine(default_deadline_seconds=0)
        requested = await engine.request(
            ToolCall(tool_name="sensitive_action", arguments={})
        )
        resolved = await engine.wait_for_decision(requested.approval_id)
        assert resolved.status == ApprovalStatus.EXPIRED
        assert engine.get(requested.approval_id).status == ApprovalStatus.EXPIRED

    asyncio.run(scenario())


def test_wait_for_decision_returns_rejection_without_approval() -> None:
    async def scenario() -> None:
        engine = ApprovalEngine(default_deadline_seconds=2)
        requested = await engine.request(
            ToolCall(tool_name="sensitive_action", arguments={})
        )
        waiter = asyncio.create_task(
            engine.wait_for_decision(requested.approval_id)
        )
        await asyncio.sleep(0)
        rejected = await engine.reject(
            requested.approval_id, "reviewer-1", "not authorized"
        )
        assert rejected.status == ApprovalStatus.REJECTED
        resolved = await waiter
        assert resolved.status == ApprovalStatus.REJECTED

    asyncio.run(scenario())
