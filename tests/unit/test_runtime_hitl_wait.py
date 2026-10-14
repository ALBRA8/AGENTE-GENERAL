"""Runtime integration tests for pending human approvals."""

import asyncio
from types import SimpleNamespace

import pytest

from albra_seed.core.types import ApprovalStatus, AutonomyLevel, RunContext, ToolCall
from albra_seed.runtime.agent import Agent


class _Runtime:
    def __init__(self, approval):
        self.approval = approval

    def get_component(self, name):
        if name == "policy_engine":
            return SimpleNamespace(
                evaluate=self._require_hitl,
            )
        if name == "approval_engine":
            return self.approval
        return None

    async def _require_hitl(self, call, *, context):
        return SimpleNamespace(decision="require_hitl")


@pytest.mark.asyncio
async def test_runtime_waits_for_approval_before_allowing_tool_call():
    class _Approval:
        async def request(self, call, *, context):
            return SimpleNamespace(
                approval_id="approval-1", status=ApprovalStatus.PENDING
            )

        async def wait_for_decision(self, approval_id):
            await asyncio.sleep(0)
            return SimpleNamespace(
                approval_id=approval_id, status=ApprovalStatus.APPROVED
            )

    agent = object.__new__(Agent)
    agent.runtime = _Runtime(_Approval())
    agent.identity = SimpleNamespace(autonomy_level=AutonomyLevel.L3_EXECUTE_EXTERNAL)
    run_context = RunContext(agent_id="agent-test", task="send")
    ctx = {
        "PLAN": {"tool_calls": [ToolCall(tool_name="external.send")]},
        "context": run_context,
        "cancel_event": asyncio.Event(),
    }

    result = await agent._phase_check_permissions(agent, ctx)

    assert result["decisions"][0]["decision"] == "allow"
    assert run_context.approvals == ["approval-1"]


@pytest.mark.asyncio
async def test_runtime_denies_rejected_human_approval():
    class _Approval:
        async def request(self, call, *, context):
            return SimpleNamespace(
                approval_id="approval-2", status=ApprovalStatus.PENDING
            )

        async def wait_for_decision(self, approval_id):
            return SimpleNamespace(
                approval_id=approval_id, status=ApprovalStatus.REJECTED
            )

    agent = object.__new__(Agent)
    agent.runtime = _Runtime(_Approval())
    agent.identity = SimpleNamespace(autonomy_level=AutonomyLevel.L3_EXECUTE_EXTERNAL)
    run_context = RunContext(agent_id="agent-test", task="send")
    ctx = {
        "PLAN": {"tool_calls": [ToolCall(tool_name="external.send")]},
        "context": run_context,
        "cancel_event": asyncio.Event(),
    }

    result = await agent._phase_check_permissions(agent, ctx)

    assert result["decisions"][0]["decision"] == "deny"
    assert not run_context.approvals


@pytest.mark.asyncio
async def test_runtime_cancellation_interrupts_pending_approval():
    started = asyncio.Event()

    class _Approval:
        async def request(self, call, *, context):
            return SimpleNamespace(
                approval_id="approval-3", status=ApprovalStatus.PENDING
            )

        async def wait_for_decision(self, approval_id):
            started.set()
            await asyncio.Event().wait()

    agent = object.__new__(Agent)
    agent.runtime = _Runtime(_Approval())
    agent.identity = SimpleNamespace(autonomy_level=AutonomyLevel.L3_EXECUTE_EXTERNAL)
    run_context = RunContext(agent_id="agent-test", task="send")
    cancel_event = asyncio.Event()
    ctx = {
        "PLAN": {"tool_calls": [ToolCall(tool_name="external.send")]},
        "context": run_context,
        "cancel_event": cancel_event,
    }

    task = asyncio.create_task(agent._phase_check_permissions(agent, ctx))
    await started.wait()
    cancel_event.set()
    result = await asyncio.wait_for(task, timeout=1)

    assert result["decisions"][0]["decision"] == "deny"
    assert ctx["abort"] is True
