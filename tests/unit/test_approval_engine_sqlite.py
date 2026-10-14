from __future__ import annotations

import asyncio

from albra_seed.approvals import ApprovalEngine, SQLiteApprovalStore
from albra_seed.core.types import ApprovalStatus, ToolCall


def test_engine_uses_sqlite_for_durable_approval_and_call_link(tmp_path):
    path = tmp_path / "approvals.sqlite"
    first = ApprovalEngine(store=SQLiteApprovalStore(path))
    call = ToolCall(tool_name="tool.external.send", arguments={"message": "hello"})
    pending = asyncio.run(first.request(call))
    assert first.get(pending.approval_id).context_ref["call_id"] == call.id

    second = ApprovalEngine(store=SQLiteApprovalStore(path))
    approved = asyncio.run(second.approve(pending.approval_id, approver="reviewer"))
    assert approved.status == ApprovalStatus.APPROVED

    restarted = ApprovalEngine(store=SQLiteApprovalStore(path))
    restored = restarted.get(pending.approval_id)
    assert restored.status == ApprovalStatus.APPROVED
    assert restored.context_ref["call_id"] == call.id
    assert restarted.store.prepare_dispatch(pending.approval_id, call.id)
    assert restarted.store.claim_dispatch(call.id, "worker-1")
    assert not restarted.store.claim_dispatch(call.id, "worker-2")
