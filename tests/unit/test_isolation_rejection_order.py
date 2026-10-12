"""VERIFY-ISOLATION — Verify the P0-A coherence check raises BEFORE any
memory operation runs.

The P0-A fix (see ``/home/z/my-project/agent-ctx/P0-A-context-isolation-fixer.md``
and ``tests/unit/test_context_isolation.py``) added a coherence check at the
top of ``Agent.run()``: when BOTH a pre-built ``RunContext`` and a ``Session``
are supplied, their isolation dimensions (``agent_id`` / ``user_id`` /
``context_id`` / ``domain``) MUST agree — otherwise ``ValueError`` is raised
before the agent loop is entered.

This file closes a stronger verification gap: the existing tests assert that
``ValueError`` is raised AND that the memory store ends up empty. They do
NOT directly assert that NO method on the ``MemoryManager`` was invoked
during the failed run. A future regression could, e.g., move the coherence
check below ``_phase_reason`` (where ``MemoryRetriever`` calls
``mgr.recall(...)``) — the store would still end up empty (no records
written), but the perimeter would have already consulted memory with a
split source-of-truth.

Each test in this file installs a ``_SpyMemoryManager`` that records EVERY
public method call (``remember`` / ``recall`` / ``forget`` / ``consolidate``
/ ``cleanup_working`` / ``get`` / ``count``). The mismatch tests then assert
that the spy's call log is EMPTY — proving the coherence check fired before
ANY memory operation. The compatible test asserts the call log is NON-empty
AND that ``remember`` / ``recall`` / ``cleanup_working`` are each present —
proving memory operations DID happen on the happy path.

Coverage:
    1. test_mismatched_user_id_raises_before_memory_ops
    2. test_mismatched_context_id_raises_before_memory_ops
    3. test_mismatched_domain_raises_before_memory_ops
    4. test_mismatched_agent_id_raises_before_memory_ops
    5. test_compatible_context_session_proceeds_normally

CRITICAL invariants:
    * Mock provider only — NO real LLM calls.
    * All async — pytest configured with ``asyncio_mode = "auto"``.
    * Source code is NOT modified — only tests added.
    * No existing tests weakened.
"""

from __future__ import annotations

from typing import Any

import pytest

from albra_seed.core.base import LLMProvider
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import (
    AgentResponse,
    AutonomyLevel,
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
from albra_seed.memory.consolidation import ConsolidationResult
from albra_seed.runtime import Agent, Runtime
from albra_seed.runtime.runtime import COMPONENT_MEMORY_MANAGER

# =====================================================================
# Spy MemoryManager — records every public method call
# =====================================================================


class _SpyMemoryManager(MemoryManager):
    """``MemoryManager`` subclass that records every public method call.

    Each entry in ``self.calls`` is a ``(method_name, kwargs_dict)`` tuple.
    Tests assert on the call log AFTER ``agent.run()`` returns or raises:

      * On a coherence-check rejection: ``calls`` MUST be empty — no
        ``remember`` / ``recall`` / ``cleanup_working`` / etc. fired.
      * On a successful run: ``calls`` MUST contain at least one entry
        from each of ``recall`` (retriever in ``_phase_reason``),
        ``remember`` (RECORD phase), and ``cleanup_working`` (the
        ``finally`` block at the end of ``Agent.run()``).

    The spy is a THIN wrapper — it delegates to ``super()`` for actual
    behavior, so the manager still writes to the underlying store. Tests
    that ONLY want to inspect the call log can ignore the store state.
    """

    def __init__(self, store: InMemoryMemoryStore) -> None:
        super().__init__(store=store)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def remember(self, **kwargs: Any) -> Any:  # type: ignore[override]
        self.calls.append(("remember", dict(kwargs)))
        return await super().remember(**kwargs)

    async def recall(self, **kwargs: Any) -> Any:  # type: ignore[override]
        self.calls.append(("recall", dict(kwargs)))
        return await super().recall(**kwargs)

    async def forget(self, record_id: str, **kwargs: Any) -> Any:  # type: ignore[override]
        self.calls.append(("forget", {"record_id": record_id, **kwargs}))
        return await super().forget(record_id, **kwargs)

    async def consolidate(self, record_id: str, **kwargs: Any) -> ConsolidationResult:  # type: ignore[override]
        self.calls.append(("consolidate", {"record_id": record_id, **kwargs}))
        return await super().consolidate(record_id, **kwargs)

    async def cleanup_working(self, **kwargs: Any) -> int:  # type: ignore[override]
        self.calls.append(("cleanup_working", dict(kwargs)))
        return await super().cleanup_working(**kwargs)

    async def get(self, record_id: str) -> Any:  # type: ignore[override]
        self.calls.append(("get", {"record_id": record_id}))
        return await super().get(record_id)

    async def count(self, **kwargs: Any) -> int:  # type: ignore[override]
        self.calls.append(("count", dict(kwargs)))
        return await super().count(**kwargs)


# =====================================================================
# Helpers — recording mock provider + runtime with spy manager
# =====================================================================


class _RecordingMockProvider(LLMProvider):
    """Mock provider that records received messages and echoes user input.

    Mirrors the recording provider in ``test_context_isolation.py`` so we
    can assert the provider was NEVER called on a coherence-check rejection
    (the ValueError fires before the agent loop is entered, so the provider
    cannot have been called).
    """

    name = "recording_mock"
    is_mock = True
    is_external = False

    def __init__(self) -> None:
        self.received_messages: list[list[Message]] = []
        self.calls: int = 0

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
        self.calls += 1
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
    return AgentIdentity(
        name="isolation-rejection-agent",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        providers=["recording_mock"],
    )


async def _setup_runtime(
    *,
    spy: _SpyMemoryManager,
    identity: AgentIdentity,
) -> tuple[Runtime, _RecordingMockProvider]:
    """Build a Runtime with the SPY manager registered (not the real one).

    Registering the spy via ``COMPONENT_MEMORY_MANAGER`` is the trick: the
    agent loop calls ``runtime.get_memory_manager()`` which returns the spy,
    so every memory operation flows through the spy's recording wrapper.
    """
    runtime = Runtime()
    await runtime.startup()
    recording = _RecordingMockProvider()
    runtime.register_provider(recording)
    runtime.register_memory_store("default", spy.store)
    runtime.register(COMPONENT_MEMORY_MANAGER, spy)
    return runtime, recording


# =====================================================================
# Shared assertion helper
# =====================================================================


def _assert_no_memory_ops(spy: _SpyMemoryManager, recording: _RecordingMockProvider) -> None:
    """Assert NO memory operation fired and the provider was NEVER called.

    Used by every mismatch test — the coherence check must raise ValueError
    BEFORE:
      * ``_phase_receive`` runs (it doesn't touch memory, but its execution
        would mean the loop was entered — we additionally assert the
        provider was never called as a proxy for "loop never entered"),
      * ``_phase_reason`` runs (calls ``MemoryRetriever.retrieve()`` which
        calls ``mgr.recall(...)``),
      * ``_phase_record`` runs (calls ``mgr.remember(...)`` twice),
      * the ``finally`` block in ``Agent.run()`` runs (calls
        ``mgr.cleanup_working(...)``).

    Note: the ``finally`` block is INSIDE the ``try:`` clause that comes
    AFTER the coherence check. So when the coherence check raises, the
    ``try`` block is never entered and ``cleanup_working`` cannot fire.
    """
    memory_methods = {"remember", "recall", "forget", "consolidate",
                      "cleanup_working", "get", "count"}
    memory_calls = [c for c in spy.calls if c[0] in memory_methods]
    assert memory_calls == [], (
        f"memory operations fired BEFORE the coherence check raised: "
        f"{memory_calls!r} — the ValueError MUST fire first"
    )
    # Sanity: provider was NEVER called (loop never entered).
    assert recording.calls == 0, (
        f"provider was called {recording.calls} times — the agent loop "
        f"was entered despite a coherence-check mismatch"
    )
    assert recording.received_messages == [], (
        "provider received messages — the agent loop was entered despite "
        "a coherence-check mismatch"
    )


# =====================================================================
# 1. Mismatched user_id — ValueError before ANY memory op
# =====================================================================


async def test_mismatched_user_id_raises_before_memory_ops() -> None:
    """``RunContext(user_id="alice")`` + ``Session(user_id="bob")`` must
    raise ``ValueError`` BEFORE the agent loop is entered. The spy
    manager's call log MUST be empty — no ``recall`` / ``remember`` /
    ``cleanup_working`` fired.
    """
    store = InMemoryMemoryStore()
    spy = _SpyMemoryManager(store)
    identity = _identity()
    runtime, recording = await _setup_runtime(spy=spy, identity=identity)

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

    _assert_no_memory_ops(spy, recording)
    await runtime.shutdown()


# =====================================================================
# 2. Mismatched context_id — ValueError before ANY memory op
# =====================================================================


async def test_mismatched_context_id_raises_before_memory_ops() -> None:
    """``RunContext(context_id="ctx-A")`` + ``Session(context_id="ctx-B")``
    must raise ``ValueError`` BEFORE any memory operation runs.
    """
    store = InMemoryMemoryStore()
    spy = _SpyMemoryManager(store)
    identity = _identity()
    runtime, recording = await _setup_runtime(spy=spy, identity=identity)

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

    _assert_no_memory_ops(spy, recording)
    await runtime.shutdown()


# =====================================================================
# 3. Mismatched domain — ValueError before ANY memory op
# =====================================================================


async def test_mismatched_domain_raises_before_memory_ops() -> None:
    """``RunContext(domain="prod")`` + ``Session(domain="dev")`` must raise
    ``ValueError`` BEFORE any memory operation runs.

    Domain is mandatory on both sides (defaults to ``"default"``), so any
    direct mismatch is a real conflict — no None-vs-value ambiguity.
    """
    store = InMemoryMemoryStore()
    spy = _SpyMemoryManager(store)
    identity = _identity()
    runtime, recording = await _setup_runtime(spy=spy, identity=identity)

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

    _assert_no_memory_ops(spy, recording)
    await runtime.shutdown()


# =====================================================================
# 4. Mismatched agent_id — always enforced, even with otherwise-matching dims
# =====================================================================


async def test_mismatched_agent_id_raises_before_memory_ops() -> None:
    """``context.agent_id`` MUST always equal ``self.identity.agent_id`` —
    agent_id is NOT user-overridable. Enforced unconditionally, even when
    the rest of the dims (user_id / context_id / domain) match perfectly.

    This is a defensive guard: it prevents a caller from accidentally
    attributing this Agent's memory writes to a DIFFERENT agent_id — which
    would route records to the wrong agent's isolation bucket.
    """
    store = InMemoryMemoryStore()
    spy = _SpyMemoryManager(store)
    identity = _identity()
    runtime, recording = await _setup_runtime(spy=spy, identity=identity)

    session = Session(
        agent_id=identity.agent_id,
        user_id="alice",
        context_id="ctx-alice",
        domain="default",
    )
    # Note: context.agent_id="other-agent" — does NOT match the Agent's
    # identity. Every other dim matches; only agent_id is wrong.
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

    _assert_no_memory_ops(spy, recording)

    # Extra: no record was written to EITHER the real agent's bucket OR
    # the mismatched "other-agent" bucket.
    real_recs = await spy.recall(agent_id=identity.agent_id, limit=50)
    other_recs = await spy.recall(agent_id="other-agent", limit=50)
    # NOTE: the recall() calls above are made AFTER the run, so they ARE
    # legitimately in the spy's log. Filter them out for the no-op-during-
    # run assertion (which we already made above).
    real_recs = [r for r in real_recs if r.record_id]
    other_recs = [r for r in other_recs if r.record_id]
    # Drop the recall bookkeeping entries above by re-checking pre-recall
    # state: the store should have ZERO records of any memory_type.
    all_recs = await spy.store.query(agent_id=identity.agent_id, limit=50)
    other_all = await spy.store.query(agent_id="other-agent", limit=50)
    assert all_recs == [], (
        f"records written to real agent bucket during mismatched run: "
        f"{all_recs!r}"
    )
    assert other_all == [], (
        f"records written to mismatched agent bucket during run: {other_all!r}"
    )
    await runtime.shutdown()


# =====================================================================
# 5. Compatible context + session — proceeds normally, memory ops DO happen
# =====================================================================


async def test_compatible_context_session_proceeds_normally() -> None:
    """When BOTH ``session`` and ``context`` are provided AND their isolation
    dims match, the run succeeds. The spy's call log MUST be non-empty AND
    contain at least one entry from each of:

      * ``recall`` (called by ``MemoryRetriever.retrieve()`` in
        ``_phase_reason`` — even on an empty store, the retriever issues
        a recall before ranking),
      * ``remember`` (called by ``_phase_record`` — twice: once for the
        WORKING structured content, once for the EPISODIC raw user input),
      * ``cleanup_working`` (called in the ``finally`` block of
        ``Agent.run()`` — garbage-collects the WORKING memory just written).

    This is the positive control for the mismatch tests: it proves the
    spy IS wired in correctly (memory operations flow through it on the
    happy path), so the empty-call-log assertions in tests 1-4 are
    meaningful — they assert "the check fired first", not "the spy is
    broken".
    """
    store = InMemoryMemoryStore()
    spy = _SpyMemoryManager(store)
    identity = _identity()
    runtime, recording = await _setup_runtime(spy=spy, identity=identity)

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

    # No ValueError — the run succeeded and the provider was called once.
    assert response.content == "[mock] hello"
    assert recording.calls == 1, (
        f"provider must be called exactly once on the happy path, got "
        f"{recording.calls}"
    )

    # Memory operations DID happen. Group calls by method name.
    called_methods = {name for name, _kwargs in spy.calls}
    assert "recall" in called_methods, (
        f"recall must be called by MemoryRetriever in _phase_reason — "
        f"got calls={spy.calls!r}"
    )
    assert "remember" in called_methods, (
        f"remember must be called by _phase_record — got calls={spy.calls!r}"
    )
    assert "cleanup_working" in called_methods, (
        f"cleanup_working must be called in the finally block of "
        f"Agent.run() — got calls={spy.calls!r}"
    )

    # Sanity: the remember() calls wrote to alice's bucket.
    remember_calls = [k for name, k in spy.calls if name == "remember"]
    for kwargs in remember_calls:
        assert kwargs.get("agent_id") == identity.agent_id
        assert kwargs.get("user_id") == "alice"
        assert kwargs.get("context_id") == "ctx-alice"
        assert kwargs.get("domain") == "default"

    # Sanity: cleanup_working ran scoped to this execution.
    cleanup_calls = [k for name, k in spy.calls if name == "cleanup_working"]
    assert len(cleanup_calls) == 1, (
        f"cleanup_working must be called exactly once in the finally "
        f"block, got {len(cleanup_calls)} calls"
    )
    cleanup_kwargs = cleanup_calls[0]
    assert cleanup_kwargs.get("agent_id") == identity.agent_id
    assert cleanup_kwargs.get("user_id") == "alice"
    assert cleanup_kwargs.get("context_id") == "ctx-alice"
    assert cleanup_kwargs.get("execution_id") == ctx.execution_id

    await runtime.shutdown()
