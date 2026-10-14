"""Integration regressions for durable HITL decisions across engine instances."""

import asyncio
from datetime import timedelta

from albra_seed.approvals.engine import ApprovalEngine
from albra_seed.approvals.sqlite_store import SQLiteSuspendedExecutionCoordinator
from albra_seed.core.types import ApprovalStatus, RunContext, ToolCall, utc_now


def test_independent_engine_decision_reaches_original_waiter_and_claims_once(tmp_path):
    database = tmp_path / "hitl-integration.sqlite3"
    origin_store = SQLiteSuspendedExecutionCoordinator(database)
    reviewer_store = SQLiteSuspendedExecutionCoordinator(database)
    origin = ApprovalEngine(suspension_store=origin_store)
    reviewer = ApprovalEngine(suspension_store=reviewer_store)

    context = RunContext(agent_id="integration-agent", task="send test message")
    call = ToolCall(
        id="call-integration-1",
        tool_name="send_message",
        arguments={"to": "recipient@example.test", "body": "safe test"},
    )

    async def scenario():
        pending = await origin.request(
            call, context=context, deadline=utc_now() + timedelta(minutes=2)
        )
        assert pending.status is ApprovalStatus.PENDING

        waiter = asyncio.create_task(origin.wait_for_decision(pending.approval_id))
        # A separate engine/store instance models an API worker handling the
        # authenticated decision while the original worker owns Agent.run().
        decision = await reviewer.approve(
            pending.approval_id, "reviewer-integration", authorized=True
        )
        assert decision.status is ApprovalStatus.APPROVED

        observed = await asyncio.wait_for(waiter, timeout=2.0)
        assert observed.status is ApprovalStatus.APPROVED
        assert origin.claim_approved_tool_call(pending.approval_id, call, context)
        assert not origin.claim_approved_tool_call(pending.approval_id, call, context)

        assert origin.record_dispatch_outcome(
            pending.approval_id, call, context,
            outcome="succeeded", detail="integration test only",
        )
        outcome = origin_store.get_dispatch_outcome(pending.approval_id)
        assert outcome is not None
        assert outcome.outcome == "succeeded"

    asyncio.run(scenario())


def test_independent_engine_rejection_is_observed_and_never_claimed(tmp_path):
    database = tmp_path / "hitl-rejection.sqlite3"
    origin = ApprovalEngine(
        suspension_store=SQLiteSuspendedExecutionCoordinator(database)
    )
    reviewer = ApprovalEngine(
        suspension_store=SQLiteSuspendedExecutionCoordinator(database)
    )
    context = RunContext(agent_id="integration-agent", task="blocked operation")
    call = ToolCall(
        id="call-integration-reject",
        tool_name="delete_resource",
        arguments={"resource_id": "test-resource"},
    )

    async def scenario():
        pending = await origin.request(
            call, context=context, deadline=utc_now() + timedelta(minutes=2)
        )
        waiter = asyncio.create_task(origin.wait_for_decision(pending.approval_id))
        decision = await reviewer.reject(
            pending.approval_id, "reviewer-integration", "denied in test",
            authorized=True,
        )
        assert decision.status is ApprovalStatus.REJECTED
        observed = await asyncio.wait_for(waiter, timeout=2.0)
        assert observed.status is ApprovalStatus.REJECTED
        assert not origin.claim_approved_tool_call(pending.approval_id, call, context)

    asyncio.run(scenario())



def test_unauthorized_durable_decision_fails_closed_and_preserves_pending(tmp_path):
    import pytest

    from albra_seed.approvals.sqlite_store import SuspendedExecutionError

    database = tmp_path / "hitl-unauthorized.sqlite3"
    origin = ApprovalEngine(
        suspension_store=SQLiteSuspendedExecutionCoordinator(database)
    )
    reviewer = ApprovalEngine(
        suspension_store=SQLiteSuspendedExecutionCoordinator(database)
    )
    context = RunContext(agent_id="integration-agent", task="protected operation")
    call = ToolCall(
        id="call-unauthorized",
        tool_name="protected_tool",
        arguments={"resource_id": "protected-test-resource"},
    )

    async def scenario():
        pending = await origin.request(
            call, context=context, deadline=utc_now() + timedelta(minutes=2)
        )
        with pytest.raises(SuspendedExecutionError, match="authenticated approval permission required"):
            await reviewer.approve(
                pending.approval_id, "client-supplied-reviewer", authorized=False
            )

        # A rejected unauthenticated attempt must not consume or mutate the
        # pending record; a separately authorized decision can still win.
        record = origin_store.get(pending.approval_id)
        assert record.state.value == "awaiting_approval"

        approved = await reviewer.approve(
            pending.approval_id, "verified-reviewer", authorized=True
        )
        assert approved.status is ApprovalStatus.APPROVED
        assert approved.decided_by == "verified-reviewer"

    origin_store = origin._suspension_store
    asyncio.run(scenario())
