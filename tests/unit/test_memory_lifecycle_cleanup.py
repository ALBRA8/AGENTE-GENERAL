"""MEM-LIFE — WORKING memory lifecycle cleanup tests.

Verifies the new ``MemoryManager.cleanup_working`` method and its wiring
into ``Agent.run()`` (success / error / cancel paths).

Coverage:
    1. test_cleanup_working_deletes_only_working
    2. test_cleanup_working_respects_isolation_by_user
    3. test_cleanup_working_respects_isolation_by_execution_id
    4. test_cleanup_working_returns_count
    5. test_cleanup_working_on_agent_run_success
    6. test_cleanup_working_on_agent_run_error
    7. test_cleanup_working_on_agent_run_cancel
    8. test_cleanup_writes_tombstone_not_physical_delete

CRITICAL: every test runs with NO external credentials. All async — pytest
is configured with ``asyncio_mode = "auto"``.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from typing import Any

from albra_seed.core.base import LLMProvider
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import (
    AgentResponse,
    AutonomyLevel,
    MemoryType,
    Message,
    Role,
    RunContext,
    new_id,
)
from albra_seed.memory import (
    InMemoryMemoryStore,
    MemoryManager,
    SQLiteMemoryStore,
)
from albra_seed.runtime import Agent, Runtime
from albra_seed.runtime.loop import PhaseEvent
from albra_seed.runtime.runtime import COMPONENT_MEMORY_MANAGER

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _run(coro):
    return asyncio.run(coro)


async def _seed(
    mgr: MemoryManager,
    *,
    agent_id: str = "agent-A",
    domain: str = "default",
    user_id: str | None = None,
    context_id: str | None = None,
    execution_id: str | None = None,
    content: Any = "scratch",
    memory_type: MemoryType = MemoryType.WORKING,
    confidence: float = 0.4,
    source: str = "agent",
    source_type: str = "agent",
) -> str:
    rec = await mgr.remember(
        agent_id=agent_id,
        content=content,
        memory_type=memory_type,
        domain=domain,
        user_id=user_id,
        context_id=context_id,
        execution_id=execution_id,
        confidence=confidence,
        source=source,
        source_type=source_type,
    )
    return rec.record_id


class _RecordingMockProvider(LLMProvider):
    """Mock provider that records the messages it received.

    Identical in spirit to the one used by test_memory_reasoning_integration.
    """

    name = "recording_mock"
    is_mock = True
    is_external = False

    def __init__(self) -> None:
        self.received_messages: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        context: RunContext | None = None,
    ) -> AgentResponse:
        self.received_messages.append(
            [
                Message(
                    role=m.role,
                    content=m.content,
                    name=m.name,
                    tool_call_id=m.tool_call_id,
                    tool_calls=m.tool_calls,
                    metadata=dict(m.metadata),
                    timestamp=m.timestamp,
                    correlation_id=m.correlation_id,
                )
                for m in messages
            ]
        )
        user_text = ""
        for msg in reversed(messages):
            if msg.role == Role.USER:
                user_text = msg.content
                break
        content = "[mock] " + user_text if user_text else "[mock] no-input"
        return AgentResponse(
            content=content,
            execution_id=context.execution_id if context else new_id("exec"),
            correlation_id=context.correlation_id if context else new_id("corr"),
            confidence=1.0,
            metadata={"provider": self.name, "mock": True},
        )

    async def health(self) -> bool:
        return True


def _identity() -> AgentIdentity:
    """Returns a NEW identity each call — callers that need to share the
    identity across multiple components MUST capture the return value
    rather than calling this multiple times.
    """
    return AgentIdentity(
        name="mem-life-agent",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        providers=["recording_mock"],
    )


async def _setup_runtime(
    *,
    manager: MemoryManager,
    identity: AgentIdentity,
) -> tuple[Runtime, _RecordingMockProvider]:
    """Build a Runtime with auto-provisioned router + Mock, a registered
    MemoryManager, and a recording mock provider for assertions.
    """
    runtime = Runtime()
    await runtime.startup()
    recording = _RecordingMockProvider()
    runtime.register_provider(recording)
    runtime.register_memory_store("default", manager.store)
    runtime.register(COMPONENT_MEMORY_MANAGER, manager)
    return runtime, recording


# --------------------------------------------------------------------------- #
# 1. cleanup_working deletes only WORKING memories
# --------------------------------------------------------------------------- #


async def test_cleanup_working_deletes_only_working() -> None:
    """A WORKING, an EPISODIC, and a FACTUAL memory are written into the same
    isolation bucket. cleanup_working deletes ONLY the WORKING one.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    await _seed(mgr, memory_type=MemoryType.WORKING, content="w")
    await _seed(mgr, memory_type=MemoryType.EPISODIC, content="e")
    # FACTUAL requires evidence + verified source + confidence >= 0.9.
    await _seed(
        mgr,
        memory_type=MemoryType.FACTUAL,
        content="f",
        confidence=0.95,
        source="tool",
        source_type="tool",
    )

    # Sanity check.
    recs = await mgr.recall(agent_id="agent-A", limit=10)
    assert len(recs) == 3

    deleted = await mgr.cleanup_working(agent_id="agent-A")
    assert deleted == 1

    # Only WORKING is gone; EPISODIC + FACTUAL survive.
    recs = await mgr.recall(agent_id="agent-A", limit=10)
    types = {r.memory_type for r in recs}
    assert MemoryType.WORKING not in types
    assert MemoryType.EPISODIC in types
    assert MemoryType.FACTUAL in types


# --------------------------------------------------------------------------- #
# 2. cleanup_working respects isolation by user_id
# --------------------------------------------------------------------------- #


async def test_cleanup_working_respects_isolation_by_user() -> None:
    """User A's WORKING memory is NOT deleted when cleanup is called for
    user B — even though both share the same agent_id + domain."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    await _seed(mgr, user_id="alice", content="alice-w")
    await _seed(mgr, user_id="bob", content="bob-w")

    # Cleanup only Bob's bucket.
    deleted = await mgr.cleanup_working(agent_id="agent-A", user_id="bob")
    assert deleted == 1

    # Alice's WORKING memory survives.
    alice = await mgr.recall(agent_id="agent-A", user_id="alice", limit=10)
    assert len(alice) == 1
    assert alice[0].memory_type == MemoryType.WORKING
    assert alice[0].content == "alice-w"
    # Bob's bucket is empty.
    bob = await mgr.recall(agent_id="agent-A", user_id="bob", limit=10)
    assert len(bob) == 0


# --------------------------------------------------------------------------- #
# 3. cleanup_working respects isolation by execution_id
# --------------------------------------------------------------------------- #


async def test_cleanup_working_respects_isolation_by_execution_id() -> None:
    """Only WORKING records with the matching execution_id are deleted."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    await _seed(mgr, execution_id="exec-1", content="w1")
    await _seed(mgr, execution_id="exec-1", content="w2")
    await _seed(mgr, execution_id="exec-2", content="w3")
    await _seed(mgr, execution_id=None, content="w-no-exec")

    deleted = await mgr.cleanup_working(
        agent_id="agent-A", execution_id="exec-1"
    )
    assert deleted == 2

    recs = await mgr.recall(agent_id="agent-A", limit=10)
    # Remaining: exec-2 record + the no-exec record.
    assert len(recs) == 2
    remaining_execs = {r.execution_id for r in recs}
    assert "exec-1" not in remaining_execs
    assert "exec-2" in remaining_execs
    assert None in remaining_execs


# --------------------------------------------------------------------------- #
# 4. cleanup_working returns the deleted count
# --------------------------------------------------------------------------- #


async def test_cleanup_working_returns_count() -> None:
    """cleanup_working returns the number of records deleted."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    for _ in range(3):
        await _seed(mgr, content="w")

    deleted = await mgr.cleanup_working(agent_id="agent-A")
    assert deleted == 3


# --------------------------------------------------------------------------- #
# 5. cleanup runs at the end of a SUCCESSFUL agent.run()
# --------------------------------------------------------------------------- #


async def test_cleanup_working_on_agent_run_success() -> None:
    """A successful agent.run() cycle leaves NO WORKING memories behind in
    its execution_id bucket, but EPISODIC memories survive.

    Pre-writes two unrelated WORKING memories:
        * one with a DIFFERENT execution_id (must survive)
        * one with execution_id=None (must survive — different exec)
    Then runs the agent — RECORD writes a WORKING + an EPISODIC for THIS
    execution. After the run returns, the agent's WORKING is gone but the
    EPISODIC and the two pre-writes survive.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    # Capture ONE identity instance — do NOT call _identity() again below.
    identity = _identity()
    runtime, _ = await _setup_runtime(manager=mgr, identity=identity)

    # Pre-write two unrelated WORKING memories in the same isolation bucket
    # but with different execution_ids — they MUST survive the agent's own
    # cleanup (which is scoped to the agent's own execution_id).
    await _seed(
        mgr,
        agent_id=identity.agent_id,
        execution_id="other-exec",
        content="other-exec-w",
    )
    await _seed(
        mgr,
        agent_id=identity.agent_id,
        execution_id=None,
        content="no-exec-w",
    )

    agent = Agent(identity, runtime)
    response = await agent.run("hello world")

    # The run succeeded — Mock echoed the user text.
    assert response.content == "[mock] hello world"

    # Inspect the agent's execution_id (stashed on the state).
    ctx = agent.state.current_context
    assert ctx is not None
    exec_id = ctx.execution_id

    # ALL WORKING memories with the agent's execution_id are gone.
    recs_all = await mgr.recall(agent_id=identity.agent_id, limit=50)
    work_for_this_exec = [
        r
        for r in recs_all
        if r.memory_type == MemoryType.WORKING and r.execution_id == exec_id
    ]
    assert work_for_this_exec == []

    # EPISODIC memory from the run survives.
    epi_for_this_exec = [
        r
        for r in recs_all
        if r.memory_type == MemoryType.EPISODIC and r.execution_id == exec_id
    ]
    assert len(epi_for_this_exec) == 1
    assert epi_for_this_exec[0].content == "hello world"

    # The two pre-writes (different execution_id) survived.
    survivors = [
        r for r in recs_all if r.memory_type == MemoryType.WORKING
    ]
    assert len(survivors) == 2
    survivor_execs = {r.execution_id for r in survivors}
    assert "other-exec" in survivor_execs
    assert None in survivor_execs

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 6. cleanup runs even when a phase raises (agent.run() does NOT crash)
# --------------------------------------------------------------------------- #


class _RespondFailsAgent(Agent):
    """Agent whose RESPOND phase always raises.

    The loop catches the exception (recorded as PhaseStatus.FAILURE), then
    _build_response falls back to the RECEIVE content, and the agent.run()
    returns an AgentResponse. The try/finally in agent.run() still runs
    cleanup_working — that is what this test verifies.
    """

    async def _phase_respond(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("simulated RESPOND failure")


async def test_cleanup_working_on_agent_run_error() -> None:
    """An agent whose RESPOND phase raises still triggers WORKING cleanup in
    the try/finally of Agent.run().

    RECORD runs BEFORE RESPOND, so the WORKING memory has been written by
    the time the failure occurs. After the run, the WORKING memory for this
    execution_id MUST be gone.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, _ = await _setup_runtime(manager=mgr, identity=identity)

    agent = _RespondFailsAgent(identity, runtime)

    # The error inside RESPOND is caught by the loop — agent.run() returns
    # an AgentResponse, with the failure recorded in errors + the trace.
    response = await agent.run("trigger error")
    assert response is not None
    assert len(response.errors) > 0, (
        "expected the simulated RESPOND failure to surface in response.errors, "
        f"got errors={response.errors}"
    )
    # The trace stored on the RunContext records RESPOND as a FAILURE.
    ctx_after = agent.state.current_context
    assert ctx_after is not None
    trace = ctx_after.outputs.get("trace", [])
    respond_phase = next(
        (t for t in trace if t.get("phase") == "RESPOND"), None
    )
    assert respond_phase is not None, (
        f"expected a RESPOND entry in the trace, got trace={trace}"
    )
    assert respond_phase["status"] == "failure"
    assert "simulated RESPOND failure" in respond_phase["error"]

    ctx = agent.state.current_context
    assert ctx is not None
    exec_id = ctx.execution_id

    # WORKING memory from this execution is gone (cleanup ran in finally).
    recs_all = await mgr.recall(agent_id=identity.agent_id, limit=50)
    work_for_this_exec = [
        r
        for r in recs_all
        if r.memory_type == MemoryType.WORKING and r.execution_id == exec_id
    ]
    assert work_for_this_exec == [], (
        f"WORKING memories for execution {exec_id} should have been cleaned up, "
        f"but found {len(work_for_this_exec)}"
    )

    # The EPISODIC memory (also written by RECORD) survived — cleanup only
    # touches WORKING memories.
    epi_for_this_exec = [
        r
        for r in recs_all
        if r.memory_type == MemoryType.EPISODIC and r.execution_id == exec_id
    ]
    assert len(epi_for_this_exec) == 1

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 7. cleanup runs after a CANCEL (cancel_event aborts EXECUTE)
# --------------------------------------------------------------------------- #


async def test_cleanup_working_on_agent_run_cancel() -> None:
    """Cancelling a run via cancel_event (soft cancel — EXECUTE returns
    aborted=True) STILL triggers the WORKING memory cleanup in finally.

    The loop continues past EXECUTE (the abort is not a hard failure) so
    RECORD runs and writes the WORKING memory. Then the try/finally in
    Agent.run() cleans it up.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, _ = await _setup_runtime(manager=mgr, identity=identity)

    agent = Agent(identity, runtime)

    # Use a phase hook to set the cancel_event AFTER the PLAN phase
    # completes — that way CHECK_PERMISSIONS runs (no-op without tools),
    # then EXECUTE sees the event set and returns aborted.
    def _hook(event: PhaseEvent) -> None:
        if event.phase == "PLAN" and event.status.value == "success":
            # Look up the cancel event for this execution_id from the agent's
            # internal map. We need the agent reference — captured by closure.
            ev = agent._cancel_events.get(event.execution_id)  # noqa: SLF001
            if ev is not None:
                ev.set()

    runtime.add_phase_hook(_hook)
    response = await agent.run("please cancel me")

    # The loop continued past EXECUTE; RECORD wrote + the run completed.
    assert response is not None
    # The hook fired AFTER PLAN completed. EXECUTE returned aborted=True
    # but the loop does not break on a successful-but-aborted phase.

    ctx = agent.state.current_context
    assert ctx is not None
    exec_id = ctx.execution_id

    # WORKING memory from this execution is gone (cleanup ran in finally).
    recs_all = await mgr.recall(agent_id=identity.agent_id, limit=50)
    work_for_this_exec = [
        r
        for r in recs_all
        if r.memory_type == MemoryType.WORKING and r.execution_id == exec_id
    ]
    assert work_for_this_exec == [], (
        f"WORKING memories for cancelled execution {exec_id} should have been "
        f"cleaned up, but found {len(work_for_this_exec)}"
    )

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 8. cleanup writes a tombstone (SQLite) — not a physical delete
# --------------------------------------------------------------------------- #


async def test_cleanup_writes_tombstone_not_physical_delete() -> None:
    """On SQLiteMemoryStore, cleanup_working calls store.delete() which:
        * removes the row from memory_records (so recall() can't find it)
        * writes a row into memory_tombstones (the audit trail)
    This is consistent with forget()'s tombstone behavior.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mem.db")
        store = SQLiteMemoryStore(db_path=path)
        mgr = MemoryManager(store=store)

        # Write 2 WORKING memories.
        rid_a = await _seed(
            mgr,
            agent_id="agent-A",
            execution_id="exec-1",
            content="w1",
        )
        rid_b = await _seed(
            mgr,
            agent_id="agent-A",
            execution_id="exec-1",
            content="w2",
        )

        # Sanity: both are retrievable.
        assert await store.read(rid_a) is not None
        assert await store.read(rid_b) is not None

        deleted = await mgr.cleanup_working(
            agent_id="agent-A", execution_id="exec-1"
        )
        assert deleted == 2

        # Records are gone from the live table — recall cannot find them.
        assert await store.read(rid_a) is None
        assert await store.read(rid_b) is None
        recs = await mgr.recall(agent_id="agent-A", limit=10)
        assert recs == []

        # Tombstones exist in the SQLite memory_tombstones table.
        conn = sqlite3.connect(path)
        try:
            cur = conn.execute(
                "SELECT record_id, reason FROM memory_tombstones "
                "WHERE record_id IN (?, ?) ORDER BY record_id",
                (rid_a, rid_b),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        assert len(rows) == 2
        tombstoned_ids = {row[0] for row in rows}
        assert rid_a in tombstoned_ids
        assert rid_b in tombstoned_ids
        # The cleanup_working path stamps the reason field on the tombstone.
        for _rid, reason in rows:
            assert reason == "working_cleanup"

        store.close()
