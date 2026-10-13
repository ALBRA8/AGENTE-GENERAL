"""P0-A — RunContext/Session isolation dimension coherence tests.

Verifies the P0-A fix: when ``Agent.run()`` receives BOTH a pre-built
``RunContext`` and a ``Session``, their isolation dimensions MUST match —
otherwise ``ValueError`` is raised BEFORE the agent loop runs. Previously,
when ``context`` was provided, ``session``'s ``user_id`` / ``context_id`` /
``domain`` were silently ignored, allowing a caller to pass
``RunContext(user_id="alice")`` + ``Session(user_id="bob")`` and silently
route memory operations to "alice" while ``session.messages.append`` attributed
to "bob". This file closes that gap.

Coverage:
    1.  test_run_with_context_only_uses_context_dims
    2.  test_run_with_session_only_uses_session_dims
    3.  test_run_with_matching_context_and_session_succeeds
    4.  test_run_with_mismatched_user_id_raises
    5.  test_run_with_mismatched_context_id_raises
    6.  test_run_with_mismatched_domain_raises
    7.  test_run_with_mismatched_agent_id_raises
    8.  test_run_with_none_dims_uses_defaults
    9.  test_concurrent_users_no_isolation_leak
    10. test_error_path_preserves_isolation
    11. test_cancel_path_preserves_isolation

CRITICAL invariants:
    * Every test uses Mock provider + synthetic data — NO real LLM calls.
    * Every test runs with NO external credentials (CORE_ONLY safe).
    * All async — pytest is configured with ``asyncio_mode = "auto"``.
    * The fix is ADDITIVE: existing tests must continue to pass.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from albra_seed.core.base import LLMProvider
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import (
    AgentResponse,
    AutonomyLevel,
    MemoryType,
    Message,
    Role,
    RunContext,
    Session,
    new_id,
)
from albra_seed.memory import (
    InMemoryMemoryStore,
    MemoryManager,
)
from albra_seed.runtime import Agent, Runtime
from albra_seed.runtime.loop import PhaseEvent
from albra_seed.runtime.runtime import COMPONENT_MEMORY_MANAGER


# --------------------------------------------------------------------------- #
# Helpers (mirrors test_memory_lifecycle_cleanup.py for consistency)
# --------------------------------------------------------------------------- #


class _RecordingMockProvider(LLMProvider):
    """Mock provider that records the messages it received and echoes the
    last USER message back with a ``[mock]`` prefix.

    Identical in spirit to the recording providers used by the memory
    integration tests — kept inline here so this file is self-contained.
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
        # Deep-copy the messages so later mutation doesn't lie about what was
        # actually seen by the provider at call time.
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
    """A fresh AgentIdentity per call. Callers that need to share an identity
    across multiple components MUST capture the return value.
    """
    return AgentIdentity(
        name="ctx-iso-agent",
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


async def _seed(
    mgr: MemoryManager,
    *,
    agent_id: str,
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
    """Write a single memory record through the manager — convenience wrapper
    so each test can do its own seeding without repeating boilerplate.
    """
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


# --------------------------------------------------------------------------- #
# 1. Context-only — context is the source-of-truth for user/context/domain
# --------------------------------------------------------------------------- #


async def test_run_with_context_only_uses_context_dims() -> None:
    """When ``context`` is provided (and no ``session``), the run uses the
    context's user_id / context_id / domain for memory operations — verified
    by inspecting which isolation bucket the EPISODIC memory lands in.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, _ = await _setup_runtime(manager=mgr, identity=identity)

    ctx = RunContext(
        agent_id=identity.agent_id,
        task="hello",
        user_id="alice",
        context_id="ctx-alice",
        domain="default",
    )
    agent = Agent(identity, runtime)
    response = await agent.run("hello", context=ctx)

    assert response.content == "[mock] hello"

    # The EPISODIC memory landed in alice's bucket (user_id="alice").
    alice_recs = await mgr.recall(
        agent_id=identity.agent_id, user_id="alice", context_id="ctx-alice", limit=10
    )
    episodic_alice = [
        r for r in alice_recs if r.memory_type == MemoryType.EPISODIC
    ]
    assert len(episodic_alice) == 1
    assert episodic_alice[0].content == "hello"

    # No memory leaked into bob's bucket.
    bob_recs = await mgr.recall(
        agent_id=identity.agent_id, user_id="bob", limit=10
    )
    assert bob_recs == []

    # No memory leaked into the no-user bucket either.
    none_recs = await mgr.recall(agent_id=identity.agent_id, user_id=None, limit=10)
    assert none_recs == []

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 2. Session-only — session dims propagate into the freshly built RunContext
# --------------------------------------------------------------------------- #


async def test_run_with_session_only_uses_session_dims() -> None:
    """When only ``session`` is provided, ``session.user_id`` / ``context_id``
    / ``domain`` propagate into the freshly constructed RunContext — verified
    by inspecting which isolation bucket the EPISODIC memory lands in.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, _ = await _setup_runtime(manager=mgr, identity=identity)

    session = Session(
        agent_id=identity.agent_id,
        user_id="bob",
        context_id="ctx-bob",
        domain="default",
    )
    agent = Agent(identity, runtime)
    response = await agent.run("hello bob", session=session)

    assert response.content == "[mock] hello bob"

    bob_recs = await mgr.recall(
        agent_id=identity.agent_id, user_id="bob", context_id="ctx-bob", limit=10
    )
    episodic_bob = [r for r in bob_recs if r.memory_type == MemoryType.EPISODIC]
    assert len(episodic_bob) == 1
    assert episodic_bob[0].content == "hello bob"

    # Alice's bucket stayed empty.
    alice_recs = await mgr.recall(
        agent_id=identity.agent_id, user_id="alice", limit=10
    )
    assert alice_recs == []

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 3. Both provided and matching — succeeds, no error
# --------------------------------------------------------------------------- #


async def test_run_with_matching_context_and_session_succeeds() -> None:
    """When BOTH ``session`` and ``context`` are provided AND their isolation
    dims match, the run succeeds. Memory operations land in the agreed bucket.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, _ = await _setup_runtime(manager=mgr, identity=identity)

    session = Session(
        agent_id=identity.agent_id,
        user_id="alice",
        context_id="ctx-alice",
        domain="default",
    )
    ctx = RunContext(
        agent_id=identity.agent_id,
        session_id=session.session_id,
        task="hello",
        user_id="alice",
        context_id="ctx-alice",
        domain="default",
    )
    agent = Agent(identity, runtime)
    response = await agent.run("hello", session=session, context=ctx)

    assert response.content == "[mock] hello"

    # The EPISODIC memory landed in alice's bucket — single source of truth.
    alice_recs = await mgr.recall(
        agent_id=identity.agent_id, user_id="alice", context_id="ctx-alice", limit=10
    )
    episodic_alice = [
        r for r in alice_recs if r.memory_type == MemoryType.EPISODIC
    ]
    assert len(episodic_alice) == 1
    assert episodic_alice[0].content == "hello"

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 4. Mismatched user_id — ValueError
# --------------------------------------------------------------------------- #


async def test_run_with_mismatched_user_id_raises() -> None:
    """``RunContext(user_id="alice")`` + ``Session(user_id="bob")`` must raise
    ``ValueError`` BEFORE the agent loop runs. No memory operations may
    occur — neither alice's nor bob's bucket is touched.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, recording = await _setup_runtime(manager=mgr, identity=identity)

    session = Session(
        agent_id=identity.agent_id,
        user_id="bob",
        context_id="ctx-shared",
        domain="default",
    )
    ctx = RunContext(
        agent_id=identity.agent_id,
        session_id=session.session_id,
        task="hello",
        user_id="alice",  # MISMATCH — session says "bob"
        context_id="ctx-shared",
        domain="default",
    )
    agent = Agent(identity, runtime)
    with pytest.raises(ValueError, match="user_id"):
        await agent.run("hello", session=session, context=ctx)

    # No memory was written (the check fires before the loop runs).
    all_recs = await mgr.recall(agent_id=identity.agent_id, limit=50)
    assert all_recs == []
    # The provider was never called either.
    assert recording.received_messages == []

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 5. Mismatched context_id — ValueError
# --------------------------------------------------------------------------- #


async def test_run_with_mismatched_context_id_raises() -> None:
    """``RunContext(context_id="ctx-A")`` + ``Session(context_id="ctx-B")`` must
    raise ``ValueError`` before the loop runs.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, recording = await _setup_runtime(manager=mgr, identity=identity)

    session = Session(
        agent_id=identity.agent_id,
        user_id="alice",
        context_id="ctx-B",  # MISMATCH — context says "ctx-A"
        domain="default",
    )
    ctx = RunContext(
        agent_id=identity.agent_id,
        session_id=session.session_id,
        task="hello",
        user_id="alice",
        context_id="ctx-A",
        domain="default",
    )
    agent = Agent(identity, runtime)
    with pytest.raises(ValueError, match="context_id"):
        await agent.run("hello", session=session, context=ctx)

    all_recs = await mgr.recall(agent_id=identity.agent_id, limit=50)
    assert all_recs == []
    assert recording.received_messages == []

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 6. Mismatched domain — ValueError
# --------------------------------------------------------------------------- #


async def test_run_with_mismatched_domain_raises() -> None:
    """``RunContext(domain="prod")`` + ``Session(domain="dev")`` must raise
    ``ValueError`` before the loop runs.

    Domain is mandatory on both sides (defaults to "default"), so any direct
    mismatch is a real conflict — no None-vs-value ambiguity here.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, recording = await _setup_runtime(manager=mgr, identity=identity)

    session = Session(
        agent_id=identity.agent_id,
        user_id="alice",
        context_id="ctx-alice",
        domain="dev",  # MISMATCH — context says "prod"
    )
    ctx = RunContext(
        agent_id=identity.agent_id,
        session_id=session.session_id,
        task="hello",
        user_id="alice",
        context_id="ctx-alice",
        domain="prod",
    )
    agent = Agent(identity, runtime)
    with pytest.raises(ValueError, match="domain"):
        await agent.run("hello", session=session, context=ctx)

    all_recs = await mgr.recall(agent_id=identity.agent_id, limit=50)
    assert all_recs == []
    assert recording.received_messages == []

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 7. Mismatched agent_id — always enforced
# --------------------------------------------------------------------------- #


async def test_run_with_mismatched_agent_id_raises() -> None:
    """``context.agent_id`` MUST always equal ``self.identity.agent_id`` —
    agent_id is NOT user-overridable. Verified unconditionally, even when
    the rest of the dims match.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, recording = await _setup_runtime(manager=mgr, identity=identity)

    session = Session(
        agent_id=identity.agent_id,
        user_id="alice",
        context_id="ctx-alice",
        domain="default",
    )
    # Note: context.agent_id="other-agent" — does NOT match the Agent's
    # identity. The other dims match perfectly; only agent_id is wrong.
    ctx = RunContext(
        agent_id="other-agent",
        session_id=session.session_id,
        task="hello",
        user_id="alice",
        context_id="ctx-alice",
        domain="default",
    )
    agent = Agent(identity, runtime)
    with pytest.raises(ValueError, match="agent_id"):
        await agent.run("hello", session=session, context=ctx)

    all_recs = await mgr.recall(agent_id=identity.agent_id, limit=50)
    assert all_recs == []
    # Also the "other-agent" bucket is empty — nothing was ever written.
    other_recs = await mgr.recall(agent_id="other-agent", limit=50)
    assert other_recs == []
    assert recording.received_messages == []

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 8. Both None for user_id/context_id — defaults applied, no mismatch
# --------------------------------------------------------------------------- #


async def test_run_with_none_dims_uses_defaults() -> None:
    """When BOTH ``session`` and ``context`` have ``None`` for user_id and
    context_id (and matching default domain), the run succeeds. None on
    one side is treated as "unspecified" — not a mismatch.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, _ = await _setup_runtime(manager=mgr, identity=identity)

    session = Session(
        agent_id=identity.agent_id,
        user_id=None,
        context_id=None,
        domain="default",
    )
    ctx = RunContext(
        agent_id=identity.agent_id,
        session_id=session.session_id,
        task="hello",
        user_id=None,
        context_id=None,
        domain="default",
    )
    agent = Agent(identity, runtime)
    response = await agent.run("hello", session=session, context=ctx)

    assert response.content == "[mock] hello"

    # Memory landed in the (None, None) bucket — context is the source-of-truth
    # and its values were None (matching session's None values).
    none_recs = await mgr.recall(
        agent_id=identity.agent_id, user_id=None, context_id=None, limit=10
    )
    episodic = [r for r in none_recs if r.memory_type == MemoryType.EPISODIC]
    assert len(episodic) == 1
    assert episodic[0].content == "hello"

    # And no memory leaked into a named-user bucket.
    alice_recs = await mgr.recall(
        agent_id=identity.agent_id, user_id="alice", limit=10
    )
    assert alice_recs == []

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 9. Concurrent users — no isolation leak under asyncio.gather
# --------------------------------------------------------------------------- #


async def test_concurrent_users_no_isolation_leak() -> None:
    """User A and user B run concurrently against the same agent + same store.
    Pre-seeded SEMANTIC memories exist for both users. After both runs
    complete, neither run's retrieved memory block contains the OTHER user's
    pre-seeded record — isolation holds under concurrent execution.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, recording = await _setup_runtime(manager=mgr, identity=identity)

    # Pre-seed a distinctive SEMANTIC memory for each user (same agent_id +
    # domain; only user_id differs). These survive the WORKING cleanup at
    # the end of each run.
    await _seed(
        mgr,
        agent_id=identity.agent_id,
        user_id="alice",
        context_id=None,
        domain="default",
        execution_id=None,
        content="alice's secret preference",
        memory_type=MemoryType.SEMANTIC,
        confidence=0.8,
        source="user",
        source_type="user",
    )
    await _seed(
        mgr,
        agent_id=identity.agent_id,
        user_id="bob",
        context_id=None,
        domain="default",
        execution_id=None,
        content="bob's secret preference",
        memory_type=MemoryType.SEMANTIC,
        confidence=0.8,
        source="user",
        source_type="user",
    )

    # Two sessions, one per user — both share the agent + store.
    alice_session = Session(
        agent_id=identity.agent_id, user_id="alice", context_id=None, domain="default"
    )
    bob_session = Session(
        agent_id=identity.agent_id, user_id="bob", context_id=None, domain="default"
    )
    agent = Agent(identity, runtime)

    # Run BOTH concurrently. The agent's run() is async; both runs share the
    # same recording provider — its received_messages list will receive two
    # entries, one per run, in completion order.
    responses = await asyncio.gather(
        agent.run("what is my preference?", session=alice_session),
        agent.run("what is my preference?", session=bob_session),
    )

    assert len(responses) == 2
    for r in responses:
        assert r.content == "[mock] what is my preference?"

    # Both runs called the provider exactly once each.
    assert len(recording.received_messages) == 2

    # For each provider call, the TOOL messages must NOT contain BOTH
    # secrets — they must contain exactly the one matching the run's user.
    # M-01 fix: memory is now ``Role.TOOL``, NOT ``Role.SYSTEM``.
    alice_marker = "alice's secret preference"
    bob_marker = "bob's secret preference"
    for call_messages in recording.received_messages:
        retrieved_blob = "\n".join(
            m.content for m in call_messages if m.role == Role.TOOL
        )
        # Invariant: never both markers in the same call.
        assert not (alice_marker in retrieved_blob and bob_marker in retrieved_blob), (
            "isolation leak: a single provider call saw BOTH users' memories: "
            f"retrieved_blob={retrieved_blob!r}"
        )

    # Stronger: each call must have seen EXACTLY one marker (the matching one).
    # We don't know which call was alice vs bob — figure it out by marker.
    markers_seen = []
    for call_messages in recording.received_messages:
        retrieved_blob = "\n".join(
            m.content for m in call_messages if m.role == Role.TOOL
        )
        if alice_marker in retrieved_blob:
            markers_seen.append("alice")
        elif bob_marker in retrieved_blob:
            markers_seen.append("bob")
        else:
            markers_seen.append("none")
    assert sorted(markers_seen) == ["alice", "bob"], (
        f"expected one alice + one bob provider call, got markers_seen={markers_seen}"
    )

    # Post-run: store still isolates cleanly. Alice's bucket has her SEMANTIC
    # + her EPISODIC (from the run). Bob's bucket has his SEMANTIC + his
    # EPISODIC. Neither contains the other's records.
    alice_recs = await mgr.recall(agent_id=identity.agent_id, user_id="alice", limit=50)
    bob_recs = await mgr.recall(agent_id=identity.agent_id, user_id="bob", limit=50)

    for r in alice_recs:
        c = _stringify(r.content)
        assert bob_marker not in c, (
            f"alice bucket leaked bob's data: {c!r}"
        )
    for r in bob_recs:
        c = _stringify(r.content)
        assert alice_marker not in c, (
            f"bob bucket leaked alice's data: {c!r}"
        )

    await runtime.shutdown()


def _stringify(content: Any) -> str:
    """Flatten a record's content to a string for substring assertions."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        import json

        return json.dumps(content, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(content)


# --------------------------------------------------------------------------- #
# 10. Error path — cleanup_working still uses correct isolation dims
# --------------------------------------------------------------------------- #


class _RespondFailsAgent(Agent):
    """Agent whose RESPOND phase always raises.

    The loop catches the exception (recorded as PhaseStatus.FAILURE), then
    _build_response falls back to the RECEIVE content, and Agent.run() returns
    an AgentResponse. The try/finally in Agent.run() still runs cleanup_working
    — scoped to (agent_id, domain, user_id, context_id, execution_id).
    """

    async def _phase_respond(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("simulated RESPOND failure (error-path isolation test)")


async def test_error_path_preserves_isolation() -> None:
    """When a phase raises mid-run, the try/finally in Agent.run() still runs
    cleanup_working — and that cleanup MUST use the correct isolation dims
    (the run's user_id / context_id / domain), NOT default to None / "default".

    Setup:
        * Pre-seed a WORKING memory for ALICE  with execution_id="other-alice-exec".
        * Pre-seed a WORKING memory for BOB    with execution_id="other-bob-exec".
        * Run the agent for ALICE  (session.user_id="alice") — RECORD writes a
          WORKING for THIS execution; RESPOND fails; cleanup_working runs
          scoped to alice + her execution_id.
        * After: alice's OTHER WORKING (pre-seeded) survives; bob's WORKING
          (pre-seeded) survives entirely; only alice's THIS-exec WORKING is
          cleaned. This proves cleanup did NOT default to (None, "default").
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, _ = await _setup_runtime(manager=mgr, identity=identity)

    # Pre-seed WORKING memories in BOTH user buckets with execution_ids that
    # are NOT the agent's own — they must survive cleanup.
    await _seed(
        mgr,
        agent_id=identity.agent_id,
        user_id="alice",
        context_id=None,
        domain="default",
        execution_id="other-alice-exec",
        content="alice-other-exec-w",
    )
    await _seed(
        mgr,
        agent_id=identity.agent_id,
        user_id="bob",
        context_id=None,
        domain="default",
        execution_id="other-bob-exec",
        content="bob-other-exec-w",
    )

    # Sanity: both buckets have exactly 1 record before the run.
    pre_alice = await mgr.recall(
        agent_id=identity.agent_id, user_id="alice", limit=10
    )
    pre_bob = await mgr.recall(agent_id=identity.agent_id, user_id="bob", limit=10)
    assert len(pre_alice) == 1
    assert len(pre_bob) == 1

    agent = _RespondFailsAgent(identity, runtime)
    alice_session = Session(
        agent_id=identity.agent_id,
        user_id="alice",
        context_id=None,
        domain="default",
    )
    # The error inside RESPOND is caught by the loop — agent.run() returns an
    # AgentResponse with errors populated.
    response = await agent.run("trigger error", session=alice_session)
    assert response is not None
    assert len(response.errors) > 0, (
        "expected the simulated RESPOND failure to surface in response.errors, "
        f"got errors={response.errors}"
    )

    ctx_after = agent.state.current_context
    assert ctx_after is not None
    exec_id = ctx_after.execution_id
    # The run's user_id MUST be alice (the session's user_id, not None).
    assert ctx_after.user_id == "alice", (
        f"RunContext.user_id should be 'alice' (from session), got "
        f"{ctx_after.user_id!r}"
    )

    # Inspect alice's bucket after the run.
    alice_after = await mgr.recall(
        agent_id=identity.agent_id, user_id="alice", limit=50
    )
    alice_work = [r for r in alice_after if r.memory_type == MemoryType.WORKING]
    alice_epi = [r for r in alice_after if r.memory_type == MemoryType.EPISODIC]

    # The WORKING memory written by RECORD for THIS execution_id was cleaned
    # up (cleanup ran in finally). The pre-seeded one (other-alice-exec) MUST
    # survive — cleanup is scoped to this execution_id.
    alice_work_execs = {r.execution_id for r in alice_work}
    assert exec_id not in alice_work_execs, (
        f"WORKING for this execution ({exec_id}) should have been cleaned up, "
        f"but alice_work_execs={alice_work_execs}"
    )
    assert "other-alice-exec" in alice_work_execs, (
        "alice's pre-seeded WORKING (other-alice-exec) must survive cleanup"
    )

    # The EPISODIC memory written by RECORD survived (cleanup only touches
    # WORKING).
    alice_epi_for_this_exec = [
        r for r in alice_epi if r.execution_id == exec_id
    ]
    assert len(alice_epi_for_this_exec) == 1
    assert alice_epi_for_this_exec[0].content == "trigger error"

    # CRITICAL isolation check: BOB's bucket is UNTOUCHED. cleanup_working
    # must NOT have defaulted to (None, "default") and wiped bob's WORKING.
    bob_after = await mgr.recall(agent_id=identity.agent_id, user_id="bob", limit=10)
    bob_work = [r for r in bob_after if r.memory_type == MemoryType.WORKING]
    assert len(bob_work) == 1, (
        f"bob's WORKING memory must survive alice's error-path cleanup, "
        f"got bob_after={[(r.memory_type, r.execution_id, r.content) for r in bob_after]}"
    )
    assert bob_work[0].execution_id == "other-bob-exec"

    # And the (None, "default") bucket is empty — cleanup did NOT default.
    none_after = await mgr.recall(
        agent_id=identity.agent_id, user_id=None, limit=10
    )
    assert none_after == [], (
        "cleanup must NOT default to (None, 'default'); the no-user bucket "
        f"should be empty, got {[(r.memory_type, r.execution_id) for r in none_after]}"
    )

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# 11. Cancel path — cleanup_working still uses correct isolation dims
# --------------------------------------------------------------------------- #


async def test_cancel_path_preserves_isolation() -> None:
    """When the agent is cancelled via cancel_event, the try/finally in
    Agent.run() still runs cleanup_working — scoped to the run's isolation
    dims (alice's bucket + this execution_id). Bob's bucket and the no-user
    bucket must NOT be touched.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    identity = _identity()
    runtime, _ = await _setup_runtime(manager=mgr, identity=identity)

    # Pre-seed WORKING in BOTH user buckets with non-matching execution_ids.
    await _seed(
        mgr,
        agent_id=identity.agent_id,
        user_id="alice",
        context_id=None,
        domain="default",
        execution_id="other-alice-exec",
        content="alice-other-exec-w",
    )
    await _seed(
        mgr,
        agent_id=identity.agent_id,
        user_id="bob",
        context_id=None,
        domain="default",
        execution_id="other-bob-exec",
        content="bob-other-exec-w",
    )

    agent = Agent(identity, runtime)
    alice_session = Session(
        agent_id=identity.agent_id,
        user_id="alice",
        context_id=None,
        domain="default",
    )

    # Use a phase hook to set the cancel_event AFTER the PLAN phase
    # completes — that way CHECK_PERMISSIONS runs (no-op without tools),
    # then EXECUTE sees the event set and returns aborted. The loop
    # continues past EXECUTE; RECORD writes the WORKING memory; then the
    # try/finally in Agent.run() cleans it up.
    def _hook(event: PhaseEvent) -> None:
        if event.phase == "PLAN" and event.status.value == "success":
            ev = agent._cancel_events.get(event.execution_id)  # noqa: SLF001
            if ev is not None:
                ev.set()

    runtime.add_phase_hook(_hook)
    response = await agent.run("please cancel me", session=alice_session)
    assert response is not None

    ctx = agent.state.current_context
    assert ctx is not None
    exec_id = ctx.execution_id
    # The run's user_id MUST be alice — cleanup must use alice's bucket, not
    # default to None.
    assert ctx.user_id == "alice", (
        f"RunContext.user_id should be 'alice' (from session), got "
        f"{ctx.user_id!r}"
    )

    # Alice's WORKING for THIS execution is gone (cleanup ran in finally).
    alice_after = await mgr.recall(
        agent_id=identity.agent_id, user_id="alice", limit=50
    )
    alice_work = [r for r in alice_after if r.memory_type == MemoryType.WORKING]
    alice_work_execs = {r.execution_id for r in alice_work}
    assert exec_id not in alice_work_execs, (
        f"WORKING for cancelled execution ({exec_id}) should have been cleaned "
        f"up, but alice_work_execs={alice_work_execs}"
    )
    assert "other-alice-exec" in alice_work_execs, (
        "alice's pre-seeded WORKING (other-alice-exec) must survive cancel cleanup"
    )

    # BOB's bucket is UNTOUCHED.
    bob_after = await mgr.recall(agent_id=identity.agent_id, user_id="bob", limit=10)
    bob_work = [r for r in bob_after if r.memory_type == MemoryType.WORKING]
    assert len(bob_work) == 1, (
        f"bob's WORKING memory must survive alice's cancel cleanup, "
        f"got bob_after={[(r.memory_type, r.execution_id) for r in bob_after]}"
    )
    assert bob_work[0].execution_id == "other-bob-exec"

    # The (None, "default") bucket is empty.
    none_after = await mgr.recall(agent_id=identity.agent_id, user_id=None, limit=10)
    assert none_after == [], (
        "cleanup must NOT default to (None, 'default'); the no-user bucket "
        f"should be empty, got {[(r.memory_type, r.execution_id) for r in none_after]}"
    )

    await runtime.shutdown()
