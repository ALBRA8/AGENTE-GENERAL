"""Runtime tests for fail-closed, one-shot dispatch of durably approved HITL calls."""
import asyncio
import sqlite3

import pytest

from albra_seed.approvals.engine import ApprovalEngine
from albra_seed.approvals.sqlite_store import SQLiteApprovalStore
from albra_seed.core.types import RunContext, ToolCall, ToolResult
from albra_seed.runtime.agent import Agent
from albra_seed.runtime.runtime import COMPONENT_APPROVAL_ENGINE


class _Runtime:
    def __init__(self, approval, tool):
        self.approval = approval
        self.tool = tool

    def get_component(self, name):
        return self.approval if name == COMPONENT_APPROVAL_ENGINE else None

    def get_tool(self, name):
        return self.tool if name == self.tool.name else None


class _Tool:
    name = "external.send"

    def __init__(self, *, raises=False):
        self.calls = 0
        self.raises = raises

    async def execute(self, arguments, *, context):
        self.calls += 1
        if self.raises:
            raise TimeoutError("provider connection lost after dispatch")
        return ToolResult(call_id="call-1", success=True, output={"sent": True})


async def _approved_context(tmp_path, tool, *, arguments=None):
    store = SQLiteApprovalStore(tmp_path / "approvals.sqlite3")
    approval = ApprovalEngine(store=store)
    call = ToolCall(
        id="call-1",
        tool_name="external.send",
        arguments=arguments or {"to": "recipient@example.test", "body": "hello"},
    )
    run_context = RunContext(agent_id="agent-test", task="send a message")
    request = await approval.request(call, context=run_context)
    await approval.approve(request.approval_id, approver="reviewer-1")

    agent = object.__new__(Agent)
    agent.runtime = _Runtime(approval, tool)
    context = {
        "cancel_event": asyncio.Event(),
        "CHECK_PERMISSIONS": {
            "decisions": [{
                "tool": call.tool_name,
                "decision": "allow",
                "approval_id": request.approval_id,
            }]
        },
        "PLAN": {"tool_calls": [call]},
        "context": run_context,
    }
    return agent, context, store


@pytest.mark.asyncio
async def test_durable_approved_call_is_dispatched_only_once(tmp_path):
    tool = _Tool()
    agent, context, store = await _approved_context(tmp_path, tool)

    first = await agent._phase_execute(agent, context)
    second = await agent._phase_execute(agent, context)

    assert first["results"][0].success is True
    assert second["results"][0].success is False
    assert "already claimed" in second["results"][0].error
    assert tool.calls == 1
    with sqlite3.connect(store.db_path) as connection:
        state = connection.execute(
            "SELECT state FROM approval_dispatch WHERE call_id = ?", ("call-1",)
        ).fetchone()[0]
    assert state == "completed"


@pytest.mark.asyncio
async def test_ambiguous_tool_failure_is_not_retried(tmp_path):
    tool = _Tool(raises=True)
    agent, context, store = await _approved_context(tmp_path, tool)

    first = await agent._phase_execute(agent, context)
    second = await agent._phase_execute(agent, context)

    assert first["results"][0].success is False
    assert second["results"][0].success is False
    assert tool.calls == 1
    with sqlite3.connect(store.db_path) as connection:
        state = connection.execute(
            "SELECT state FROM approval_dispatch WHERE call_id = ?", ("call-1",)
        ).fetchone()[0]
    assert state == "uncertain"


@pytest.mark.asyncio
async def test_changed_arguments_cannot_use_an_existing_approval(tmp_path):
    tool = _Tool()
    agent, context, store = await _approved_context(tmp_path, tool)
    context["PLAN"]["tool_calls"][0].arguments = {
        "to": "attacker@example.test", "body": "changed"
    }

    result = await agent._phase_execute(agent, context)

    assert result["results"][0].success is False
    assert "durable dispatch guard failed" in result["results"][0].error
    assert tool.calls == 0
    with sqlite3.connect(store.db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM approval_dispatch WHERE call_id = ?", ("call-1",)
        ).fetchone()[0]
    assert count == 0



@pytest.mark.asyncio
async def test_ambiguous_dispatch_is_not_retried_after_store_and_engine_restart(tmp_path):
    """Reopening SQLite after an ambiguous provider failure must not replay the call."""
    tool = _Tool(raises=True)
    agent, context, store = await _approved_context(tmp_path, tool)

    first = await agent._phase_execute(agent, context)
    assert first["results"][0].success is False
    assert tool.calls == 1

    # Simulate a worker restart by constructing a new store and engine over the
    # same durable database. The original call and approval evidence are unchanged.
    recovered_store = SQLiteApprovalStore(store.db_path)
    recovered_approval = ApprovalEngine(store=recovered_store)
    agent.runtime.approval = recovered_approval

    second = await agent._phase_execute(agent, context)

    assert second["results"][0].success is False
    assert tool.calls == 1
    with sqlite3.connect(store.db_path) as connection:
        state = connection.execute(
            "SELECT state FROM approval_dispatch WHERE call_id = ?", ("call-1",)
        ).fetchone()[0]
    assert state == "uncertain"
