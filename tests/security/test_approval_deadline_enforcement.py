"""Regression tests for approval deadlines and decision waiters."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from albra_seed.approvals import ApprovalEngine
from albra_seed.core.types import ApprovalStatus, RunContext, ToolCall, utc_now


@pytest.mark.asyncio
async def test_late_approval_expires_instead_of_approving() -> None:
    engine = ApprovalEngine()
    pending = await engine.request("tool.external.http", deadline=utc_now() - timedelta(seconds=1))

    result = await engine.approve(pending.approval_id, approver="human:test")

    assert result.status == ApprovalStatus.EXPIRED
    assert result.reason == "deadline_passed"
    assert engine.get(pending.approval_id).status == ApprovalStatus.EXPIRED
    assert engine.evidence.list_for(pending.approval_id)[-1].reason == "deadline_passed"
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
    assert engine.evidence.list_for(pending.approval_id)[-1].reason == "deadline_passed"
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


@pytest.mark.asyncio
async def test_tool_approval_binds_call_id_and_canonical_arguments_digest() -> None:
    engine = ApprovalEngine()
    context = RunContext(agent_id="agent:test", task="approve tool")
    call = ToolCall(
        id="tc-bound-123",
        tool_name="tool.external.http",
        arguments={"url": "https://example.invalid", "method": "GET"},
    )

    pending = await engine.request(call, context=context)
    request = engine.get(pending.approval_id)
    binding = request.context_ref

    import hashlib
    import json

    expected = hashlib.sha256(
        json.dumps(
            call.arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert binding["execution_id"] == context.execution_id
    assert binding["correlation_id"] == context.correlation_id
    assert binding["tool_call_id"] == call.id
    assert binding["arguments_sha256"] == expected



@pytest.mark.asyncio
async def test_approved_binding_rejects_changed_call_or_execution_context() -> None:
    engine = ApprovalEngine()
    context = RunContext(agent_id="agent:test", task="approve tool")
    call = ToolCall(
        id="tc-binding-check",
        tool_name="tool.external.http",
        arguments={"url": "https://example.invalid", "method": "GET"},
    )
    pending = await engine.request(call, context=context)

    # A pending request is never sufficient to authorize dispatch.
    assert not engine.matches_approved_tool_call(pending.approval_id, call, context)

    approved = await engine.approve(pending.approval_id, approver="human:test")
    assert approved.status == ApprovalStatus.APPROVED
    assert engine.matches_approved_tool_call(pending.approval_id, call, context)

    # Canonical JSON makes key ordering irrelevant, but binds all values.
    reordered = ToolCall(
        id=call.id,
        tool_name=call.tool_name,
        arguments={"method": "GET", "url": "https://example.invalid"},
    )
    assert engine.matches_approved_tool_call(pending.approval_id, reordered, context)

    changed_arguments = ToolCall(
        id=call.id,
        tool_name=call.tool_name,
        arguments={"url": "https://attacker.invalid", "method": "GET"},
    )
    assert not engine.matches_approved_tool_call(
        pending.approval_id, changed_arguments, context
    )

    changed_id = ToolCall(
        id="tc-different",
        tool_name=call.tool_name,
        arguments=call.arguments,
    )
    assert not engine.matches_approved_tool_call(
        pending.approval_id, changed_id, context
    )

    other_context = RunContext(agent_id="agent:other", task="different execution")
    assert not engine.matches_approved_tool_call(
        pending.approval_id, call, other_context
    )



@pytest.mark.asyncio
async def test_approved_tool_call_can_be_claimed_only_once_per_execution() -> None:
    engine = ApprovalEngine()
    context = RunContext(agent_id="agent:test", task="claim tool call")
    call = ToolCall(
        id="tc-at-most-once",
        tool_name="tool.external.http",
        arguments={"url": "https://example.invalid"},
    )
    pending = await engine.request(call, context=context)

    assert not engine.claim_approved_tool_call(pending.approval_id, call, context)
    await engine.approve(pending.approval_id, approver="human:test")

    assert engine.claim_approved_tool_call(pending.approval_id, call, context)
    assert not engine.claim_approved_tool_call(pending.approval_id, call, context)



@pytest.mark.asyncio
async def test_concurrent_threads_only_claim_approved_call_once() -> None:
    engine = ApprovalEngine()
    context = RunContext(agent_id="agent:test", task="concurrent claim")
    call = ToolCall(
        id="tc-concurrent-claim",
        tool_name="tool.external.http",
        arguments={"url": "https://example.invalid"},
    )
    pending = await engine.request(call, context=context)
    await engine.approve(pending.approval_id, approver="human:test")

    # Exercise the synchronous claim operation from real threads, not just
    # coroutines on one event loop. Exactly one contender must win.
    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(
            pool.map(
                lambda _: engine.claim_approved_tool_call(
                    pending.approval_id, call, context
                ),
                range(64),
            )
        )

    assert sum(outcomes) == 1
