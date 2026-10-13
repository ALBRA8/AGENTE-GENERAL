"""ApprovalEngine integration tests for SQLite-backed cross-worker decisions."""
import asyncio
from datetime import timedelta

from albra_seed.approvals.engine import ApprovalEngine
from albra_seed.approvals.sqlite_store import SQLiteSuspendedExecutionCoordinator
from albra_seed.core.types import ApprovalStatus, RunContext, ToolCall, utc_now


def _context():
    return RunContext(
        agent_id="agent-test",
        task="test durable HITL",
    )


def test_second_engine_can_decide_and_originating_engine_resumes_same_waiter(tmp_path):
    path = tmp_path / "hitl.sqlite3"
    origin_store = SQLiteSuspendedExecutionCoordinator(path)
    reviewer_store = SQLiteSuspendedExecutionCoordinator(path)
    origin = ApprovalEngine(suspension_store=origin_store)
    reviewer = ApprovalEngine(suspension_store=reviewer_store)
    context = _context()
    call = ToolCall(
        id="tool-call-1",
        tool_name="send_message",
        arguments={"to": "person@example.test", "body": "approved payload"},
    )

    async def scenario():
        requested = await origin.request(
            call,
            context=context,
            actor="principal:requester-1",
            deadline=utc_now() + timedelta(seconds=10),
        )
        waiter = asyncio.create_task(origin.wait_for_decision(requested.approval_id))
        await asyncio.sleep(0.03)
        decided = await reviewer.approve(requested.approval_id, "reviewer-1", authorized=True)
        resumed = await waiter

        assert decided.status is ApprovalStatus.APPROVED
        assert resumed.status is ApprovalStatus.APPROVED
        assert origin.get(requested.approval_id).status is ApprovalStatus.APPROVED
        assert origin.matches_approved_tool_call(requested.approval_id, call, context)
        assert origin.claim_approved_tool_call(requested.approval_id, call, context)
        # A different engine/worker cannot claim the same dispatch a second time.
        assert not reviewer.claim_approved_tool_call(requested.approval_id, call, context)
        persisted = reviewer_store.get(requested.approval_id)
        assert persisted.state.value == "claimed"

    asyncio.run(scenario())


def test_durable_engine_requires_original_bound_call_context(tmp_path):
    store = SQLiteSuspendedExecutionCoordinator(tmp_path / "hitl.sqlite3")
    engine = ApprovalEngine(suspension_store=store)

    async def scenario():
        try:
            await engine.request("send_message", actor="principal:requester-1")
        except RuntimeError as exc:
            assert "ToolCall, RunContext" in str(exc)
        else:
            raise AssertionError("durable HITL accepted an unbound action")

    asyncio.run(scenario())



def test_durable_engine_rejects_decision_without_explicit_authorization(tmp_path):
    path = tmp_path / "hitl.sqlite3"
    store = SQLiteSuspendedExecutionCoordinator(path)
    engine = ApprovalEngine(suspension_store=store)
    context = _context()
    call = ToolCall(id="call-auth", tool_name="send_message", arguments={"body": "x"})

    async def scenario():
        requested = await engine.request(
            call, context=context, actor="principal:requester-1",
            deadline=utc_now() + timedelta(seconds=10),
        )
        try:
            await engine.approve(requested.approval_id, "reviewer-1")
        except RuntimeError as exc:
            assert "authenticated approval permission" in str(exc)
        else:
            raise AssertionError("durable decision bypassed explicit authorization")
        assert store.get(requested.approval_id).state.value == "awaiting_approval"

    asyncio.run(scenario())
