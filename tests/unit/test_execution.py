"""Unit tests for the execution subpackage."""

from __future__ import annotations

import asyncio
import json

import pytest

from albra_seed.core.types import ExecutionStatus, RunContext
from albra_seed.execution import (
    ExecutionRunner,
    ExecutionTrace,
    IdempotencyManager,
    TraceReplay,
    TraceStep,
)


# -------------------------------------------------------------------
# 1. ExecutionTrace records steps
# -------------------------------------------------------------------


def test_execution_trace_records_steps():
    trace = ExecutionTrace(agent_id="a1", task="t1")
    s1 = trace.begin_step("RECEIVE", inputs={"msg": "hi"})
    s1.add_tool_call("echo", result="hi")
    s1.finish(outputs={"received": True}, status="success")

    s2 = trace.begin_step("EXECUTE", inputs={"call": "echo"})
    s2.add_evidence("ev_1")
    s2.finish(outputs={"result": "hi"}, status="success")

    trace.finish(status=ExecutionStatus.SUCCEEDED)

    assert len(trace.steps) == 2
    assert trace.step("RECEIVE").tool_calls[0]["tool"] == "echo"
    assert trace.step("EXECUTE").evidence_ids == ["ev_1"]
    assert trace.status == ExecutionStatus.SUCCEEDED
    assert "ev_1" in trace.evidence_ids
    # duration_ms must be populated.
    assert trace.duration_ms >= 0.0
    assert all(s.duration_ms >= 0.0 for s in trace.steps)


# -------------------------------------------------------------------
# 2. ExecutionTrace JSON round-trip
# -------------------------------------------------------------------


def test_execution_trace_json_round_trip():
    trace = ExecutionTrace(agent_id="a1", task="t1")
    s1 = trace.begin_step("RECEIVE", inputs={"x": 1})
    s1.finish(outputs={"y": 2}, status="success")
    trace.finish(status=ExecutionStatus.SUCCEEDED)

    raw = trace.to_json()
    restored = ExecutionTrace.from_json(raw)

    assert restored.execution_id == trace.execution_id
    assert restored.agent_id == "a1"
    assert len(restored.steps) == 1
    assert restored.steps[0].phase == "RECEIVE"
    assert restored.steps[0].outputs == {"y": 2}
    assert restored.status == ExecutionStatus.SUCCEEDED


# -------------------------------------------------------------------
# 3. IdempotencyManager detects duplicates
# -------------------------------------------------------------------


def test_idempotency_detects_duplicates():
    mgr = IdempotencyManager()
    key = mgr.derive_key(task="send-email", inputs={"to": "x@example.com"})

    assert not mgr.is_duplicate(key)

    first = mgr.register(key, "exec_1", payload={"trace": "first"})
    assert first.execution_id == "exec_1"
    assert mgr.is_duplicate(key)

    # Second register of the same key returns the original (safe retry).
    second = mgr.register(key, "exec_2")
    assert second.execution_id == "exec_1"  # NOT exec_2

    lookup = mgr.lookup(key)
    assert lookup is not None
    assert lookup.execution_id == "exec_1"
    assert mgr.count() == 1


# -------------------------------------------------------------------
# 4. IdempotencyManager: distinct keys are not duplicates
# -------------------------------------------------------------------


def test_idempotency_distinct_keys_not_duplicate():
    mgr = IdempotencyManager()
    k1 = mgr.derive_key(task="t", inputs={"v": 1})
    k2 = mgr.derive_key(task="t", inputs={"v": 2})
    assert k1 != k2
    mgr.register(k1, "exec_a")
    assert not mgr.is_duplicate(k2)
    mgr.register(k2, "exec_b")
    assert mgr.count() == 2


# -------------------------------------------------------------------
# 5. TraceReplay reconstructs execution
# -------------------------------------------------------------------


def test_trace_replay_reconstructs_execution():
    # Build an original trace by running an ExecutionRunner with echo handlers.
    async def receive(ctx: RunContext, step: TraceStep) -> dict:
        step.add_tool_call("echo")
        return {"content": "hello"}

    async def respond(ctx: RunContext, step: TraceStep) -> dict:
        return {"content": "hello echoed", "confidence": 0.9}

    runner = ExecutionRunner(
        phase_handlers={"RECEIVE": receive, "RESPOND": respond},
        phases=("RECEIVE", "RESPOND"),
    )

    original_trace, _response = asyncio.run(runner.run("say hello"))

    # Now replay with handlers that reproduce the same outputs.
    async def replay_receive(ctx: RunContext, step: TraceStep, original: dict) -> dict:
        return {"content": original["content"]}

    async def replay_respond(ctx: RunContext, step: TraceStep, original: dict) -> dict:
        return original

    replayer = TraceReplay(
        handlers={"RECEIVE": replay_receive, "RESPOND": replay_respond}
    )
    result = asyncio.run(replayer.replay(original_trace))

    assert result.matched_phases >= 1
    assert result.is_exact is True
    assert len(result.replayed.steps) == len(original_trace.steps)
    assert result.replayed.status == original_trace.status


# -------------------------------------------------------------------
# 6. ExecutionRunner records trace + builds response
# -------------------------------------------------------------------


def test_execution_runner_records_trace_and_response():
    async def receive(ctx: RunContext, step: TraceStep) -> dict:
        return {"content": "hi"}

    async def respond(ctx: RunContext, step: TraceStep) -> dict:
        return {"content": "ok", "confidence": 1.0}

    runner = ExecutionRunner(
        phase_handlers={"RECEIVE": receive, "RESPOND": respond},
        phases=("RECEIVE", "RESPOND"),
    )
    trace, response = asyncio.run(runner.run("hello"))

    assert trace.status == ExecutionStatus.SUCCEEDED
    assert len(trace.steps) == 2
    assert response.content == "ok"
    assert response.confidence == 1.0
    assert response.execution_id == trace.execution_id


# -------------------------------------------------------------------
# 7. ExecutionRunner handles phase failure + records error
# -------------------------------------------------------------------


def test_execution_runner_handles_phase_failure():
    async def boom(ctx: RunContext, step: TraceStep) -> dict:
        raise RuntimeError("boom")

    runner = ExecutionRunner(
        phase_handlers={"RECEIVE": boom},
        phases=("RECEIVE", "RESPOND"),
    )
    trace, response = asyncio.run(runner.run("fail me"))

    assert trace.status == ExecutionStatus.FAILED
    assert any("boom" in e for e in trace.errors)
    assert response.errors  # populated from trace
