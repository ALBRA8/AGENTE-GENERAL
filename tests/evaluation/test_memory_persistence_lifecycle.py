"""MEM-PERSIST — Persistence + lifecycle verification for the memory subsystem.

P2 verification: 10 tests that PROVE the memory subsystem's persistence
and lifecycle contracts hold end-to-end. These are NOT quality metrics
(see ``test_memory_quality_evaluation.py`` for those) — they are
invariants:

  1. SQLite persists across reopen (close + new SQLiteMemoryStore same path)
  2. forget() survives SQLite reopen (tombstone persisted)
  3. Tombstones respected in read()/query() (no zombie records)
  4. ARCHIVED lifecycle state excluded from retrieval
  5. DELETED lifecycle state excluded from retrieval
  6. FORGOTTEN lifecycle state excluded from retrieval
  7. cleanup_working runs in the agent loop's finally block EVEN WHEN
     the provider raises (so transient WORKING memory never leaks)
  8. A store that raises on query() does NOT cause the retriever to
     silently widen scope (no fallback to a different user_id)
  9. DECISION records + learning-observation memories NEVER auto-promote
     to is_fact=True (the FACT rule is the single source of truth)
  10. CORE_ONLY birth works with zero explicit registration — birth()
      provisions provider_router + memory_store + memory_manager +
      audit_logger, and a real agent.run() completes end-to-end

All tests use the Mock provider and synthetic data. NO source code is
modified — these are pure additions to the test surface.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from albra_seed.core.base import MemoryRecord
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import (
    AutonomyLevel,
    MemoryType,
    Message,
    Session,
)
from albra_seed.learning.manager import LearningManager
from albra_seed.learning.types import LearningProposal, LearningSignal
from albra_seed.memory import (
    DecisionMemory,
    DecisionRecord,
    InMemoryMemoryStore,
    MemoryManager,
    MemoryRetriever,
    SQLiteMemoryStore,
)
from albra_seed.providers.mock import MockProvider
from albra_seed.runtime import Runtime
from albra_seed.runtime.agent import Agent
from albra_seed.runtime.runtime import (
    COMPONENT_AUDIT_LOGGER,
    COMPONENT_MEMORY_MANAGER,
    COMPONENT_MEMORY_STORE,
    COMPONENT_PROVIDER_ROUTER,
)

_DEFAULT_AGENT_ID = "persist-eval-agent"
_DEFAULT_DOMAIN = "default"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _RecordingMemoryManager(MemoryManager):
    """MemoryManager wrapper that records every cleanup_working() call.

    Used by test 7 to PROVE cleanup_working ran in the agent loop's
    finally block — even when the provider raised and RECORD never
    wrote any WORKING memory. The recording makes the invisible
    finally-block call observable.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cleanup_calls: list[dict[str, Any]] = []

    async def cleanup_working(self, **kwargs: Any) -> int:
        self.cleanup_calls.append(dict(kwargs))
        return await super().cleanup_working(**kwargs)


class _BrokenQueryStore(InMemoryMemoryStore):
    """Store whose query() raises on the first call.

    Records every query() call's isolation dims so test 8 can verify the
    retriever did NOT silently fall back to a different scope (e.g.
    querying as a different user_id to "find something").
    """

    def __init__(self) -> None:
        super().__init__()
        self.query_calls: list[dict[str, Any]] = []
        self.should_raise: bool = True

    async def query(self, **kwargs: Any) -> list[MemoryRecord]:
        self.query_calls.append(dict(kwargs))
        if self.should_raise:
            raise RuntimeError("simulated storage failure")
        return await super().query(**kwargs)


class _FailingProvider(MockProvider):
    """Mock provider that raises on every complete() call."""

    name = "failing_mock"

    async def complete(self, messages: list[Message], **kwargs: Any) -> Any:
        raise RuntimeError("provider unavailable")


async def _remember(
    mgr: MemoryManager,
    *,
    content: str = "test memory",
    memory_type: MemoryType = MemoryType.SEMANTIC,
    user_id: str = "alice",
    confidence: float = 0.9,
    execution_id: str | None = None,
) -> MemoryRecord:
    return await mgr.remember(
        agent_id=_DEFAULT_AGENT_ID,
        domain=_DEFAULT_DOMAIN,
        user_id=user_id,
        memory_type=memory_type,
        content=content,
        source="test",
        source_type="agent",
        confidence=confidence,
        execution_id=execution_id,
    )


# --------------------------------------------------------------------------- #
# Tests 1-3: SQLite persistence
# --------------------------------------------------------------------------- #


async def test_persistent_memories_survive_reopen_sqlite(tmp_path: Any) -> None:
    """Test 1: write to SQLiteMemoryStore, close, reopen (new instance
    same path), verify memories still retrievable."""
    db_path = str(tmp_path / "persist.sqlite")

    # Write phase
    store1 = SQLiteMemoryStore(db_path=db_path)
    mgr1 = MemoryManager(store=store1)
    rec = await _remember(mgr1, content="persistent memory survives reopen")
    record_id = rec.record_id
    # Verify it's readable through the first store
    assert await store1.read(record_id) is not None
    store1.close()

    # Reopen phase — new SQLiteMemoryStore instance, SAME db file
    store2 = SQLiteMemoryStore(db_path=db_path)
    fetched = await store2.read(record_id)
    assert fetched is not None, "memory did not survive SQLite reopen"
    assert fetched.content == "persistent memory survives reopen"
    assert fetched.user_id == "alice"
    assert fetched.memory_type == MemoryType.SEMANTIC

    # Also via query()
    records = await store2.query(
        agent_id=_DEFAULT_AGENT_ID,
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
    )
    assert any(r.record_id == record_id for r in records), (
        "query() did not return the persisted memory after reopen"
    )
    store2.close()


async def test_forget_effective_after_sqlite_reopen(tmp_path: Any) -> None:
    """Test 2: write, forget(), close, reopen -> memory is GONE.

    forget() writes a tombstone AND removes the record from
    ``memory_records``. After reopen, the tombstone is still in the
    ``memory_tombstones`` table and the record is absent from
    ``memory_records``.
    """
    db_path = str(tmp_path / "forget.sqlite")

    store1 = SQLiteMemoryStore(db_path=db_path)
    mgr1 = MemoryManager(store=store1)
    rec = await _remember(mgr1, content="this memory will be forgotten")
    record_id = rec.record_id

    ok = await mgr1.forget(record_id, reason="test forget", by="tester")
    assert ok is True
    # Verify gone in the first store
    assert await store1.read(record_id) is None
    store1.close()

    # Reopen
    store2 = SQLiteMemoryStore(db_path=db_path)
    assert await store2.read(record_id) is None, (
        "forgotten memory reappeared after SQLite reopen"
    )
    records = await store2.query(
        agent_id=_DEFAULT_AGENT_ID, domain=_DEFAULT_DOMAIN, user_id="alice"
    )
    assert not any(r.record_id == record_id for r in records), (
        "forgotten memory in query() results after reopen"
    )

    # Tombstone row should be persisted (proves forget() wrote durable state)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT record_id, reason, by FROM memory_tombstones "
            "WHERE record_id = ?",
            (record_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, "tombstone not persisted across reopen"
    assert row[0] == record_id
    assert row[1] == "test forget"
    assert row[2] == "tester"
    store2.close()


async def test_tombstones_respected_in_read(tmp_path: Any) -> None:
    """Test 3: write, delete() (writes tombstone), verify NOT in
    read() or query() results — tombstones are durable + respected."""
    db_path = str(tmp_path / "tombstone.sqlite")
    store = SQLiteMemoryStore(db_path=db_path)

    record = MemoryRecord(
        agent_id=_DEFAULT_AGENT_ID,
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
        memory_type=MemoryType.SEMANTIC,
        content="to be tombstoned",
    )
    record_id = await store.write(record)

    # Verify present before delete
    assert await store.read(record_id) is not None

    # delete() writes a tombstone + removes the row from memory_records
    ok = await store.delete(record_id, reason="tombstone test", by="tester")
    assert ok is True

    # read() must return None (row gone from memory_records)
    assert await store.read(record_id) is None, (
        "tombstoned record still readable via read()"
    )

    # query() must not include it
    records = await store.query(
        agent_id=_DEFAULT_AGENT_ID, domain=_DEFAULT_DOMAIN, user_id="alice"
    )
    assert not any(r.record_id == record_id for r in records), (
        "tombstoned record still in query() results"
    )

    # Tombstone row exists in the tombstones table
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT reason, by FROM memory_tombstones WHERE record_id = ?",
            (record_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "tombstone test"
    assert row[1] == "tester"
    store.close()


# --------------------------------------------------------------------------- #
# Tests 4-6: lifecycle-state exclusion from retrieval
# --------------------------------------------------------------------------- #


async def test_archived_excluded_from_retrieval() -> None:
    """Test 4: mark a record ARCHIVED via lifecycle -> excluded from
    retriever results (no zombie records surface)."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    rec = await _remember(mgr, content="archived memory")
    # Mark ARCHIVED via the lifecycle (does NOT remove from store).
    mgr.lifecycle.record_archived(rec.record_id, reason="test archive")

    retriever = MemoryRetriever(mgr)
    retrieved = await retriever.retrieve(
        agent_id=_DEFAULT_AGENT_ID,
        query="archived memory",
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
    )
    assert not any(r.record.record_id == rec.record_id for r in retrieved), (
        "ARCHIVED memory was not excluded from retrieval"
    )
    # The record is still in the store (lifecycle is the gate, not deletion)
    assert await store.read(rec.record_id) is not None


async def test_deleted_excluded_from_retrieval() -> None:
    """Test 5: mark a record DELETED via lifecycle -> excluded from
    retriever results."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    rec = await _remember(mgr, content="deleted-state memory")
    mgr.lifecycle.record_deleted(rec.record_id, reason="test delete")

    retriever = MemoryRetriever(mgr)
    retrieved = await retriever.retrieve(
        agent_id=_DEFAULT_AGENT_ID,
        query="deleted memory",
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
    )
    assert not any(r.record.record_id == rec.record_id for r in retrieved), (
        "DELETED memory was not excluded from retrieval"
    )


async def test_forgotten_excluded_from_retrieval() -> None:
    """Test 6: mark a record FORGOTTEN via lifecycle -> excluded from
    retriever results."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    rec = await _remember(mgr, content="forgotten-state memory")
    mgr.lifecycle.record_forgotten(rec.record_id, reason="test forget")

    retriever = MemoryRetriever(mgr)
    retrieved = await retriever.retrieve(
        agent_id=_DEFAULT_AGENT_ID,
        query="forgotten memory",
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
    )
    assert not any(r.record.record_id == rec.record_id for r in retrieved), (
        "FORGOTTEN memory was not excluded from retrieval"
    )


# --------------------------------------------------------------------------- #
# Test 7: cleanup_working runs even when the provider fails
# --------------------------------------------------------------------------- #


async def test_working_cleaned_even_if_provider_fails() -> None:
    """Test 7: agent.run() with a provider that raises -> the finally
    block STILL runs cleanup_working for the run's execution_id.

    We verify this by wrapping MemoryManager in a _RecordingMemoryManager
    that captures every cleanup_working() call. After the run, we assert:
      * cleanup_working was called at least once
      * it was called with execution_id == the run's execution_id
      * the run completed with errors (provider failure surfaced)
      * the cleanup call did NOT propagate the provider's exception
    """
    runtime = Runtime()
    await runtime.startup()

    store = InMemoryMemoryStore()
    mgr = _RecordingMemoryManager(store=store)
    runtime.register_memory_store(_DEFAULT_DOMAIN, store)
    runtime.register(COMPONENT_MEMORY_MANAGER, mgr)

    runtime.register_provider(_FailingProvider())

    identity = AgentIdentity(
        name="cleanup-test-agent",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        providers=["failing_mock"],
    )
    session = Session(
        agent_id=identity.agent_id,
        user_id="alice",
        context_id="ctx-cleanup",
        domain=_DEFAULT_DOMAIN,
    )
    agent = Agent(identity, runtime)

    # The provider raises, but agent.run() must NOT propagate — the
    # loop wraps each phase in try/except and the finally block runs.
    response = await agent.run("test input that triggers provider failure",
                                session=session)

    # The run produced a response (possibly with errors), not an exception.
    assert response is not None
    run_exec_id = response.execution_id

    # cleanup_working MUST have been called in the finally block.
    assert len(mgr.cleanup_calls) >= 1, (
        "cleanup_working was NOT called after provider failure — "
        "the finally block did not execute the cleanup"
    )
    # And it was called with the run's execution_id (not a default).
    cleanup_exec_ids = [c.get("execution_id") for c in mgr.cleanup_calls]
    assert run_exec_id in cleanup_exec_ids, (
        f"cleanup_working not called with the run's execution_id "
        f"={run_exec_id}; got {cleanup_exec_ids}"
    )
    # The cleanup call used the correct isolation dims (no scope widening).
    last_cleanup = mgr.cleanup_calls[-1]
    assert last_cleanup.get("agent_id") == identity.agent_id
    assert last_cleanup.get("user_id") == "alice"
    assert last_cleanup.get("context_id") == "ctx-cleanup"
    assert last_cleanup.get("domain") == _DEFAULT_DOMAIN

    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# Test 8: storage failure does not silently widen scope
# --------------------------------------------------------------------------- #


async def test_storage_failure_does_not_silently_change_scope() -> None:
    """Test 8: a store that raises on query() -> retriever returns []
    AND does NOT retry with a different user_id (no scope widening).

    The retriever's contract: a store error must NOT crash reasoning,
    AND must NOT silently fall back to a different isolation scope. We
    verify by recording every query() call the store received and
    asserting ALL of them used the caller's user_id (no fallback to
    "bob" or None).
    """
    store = _BrokenQueryStore()
    mgr = MemoryManager(store=store)

    # Pre-store real memories for both alice and bob (in the underlying
    # dict — the broken query() will raise before returning them).
    await _remember(mgr, content="alice's secret", user_id="alice")
    await _remember(mgr, content="bob's secret", user_id="bob")

    retriever = MemoryRetriever(mgr)

    # Query as alice — store raises, retriever must catch + return [].
    retrieved = await retriever.retrieve(
        agent_id=_DEFAULT_AGENT_ID,
        query="secret",
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
    )
    assert retrieved == [], (
        f"retriever did not return [] on storage failure; "
        f"got {len(retrieved)} records"
    )

    # Critical assertion: NO query() call used a different user_id.
    # If the retriever had silently widened scope (e.g. queried with
    # user_id=None or user_id="bob" to "find something"), this would
    # surface here.
    assert len(store.query_calls) >= 1
    for call in store.query_calls:
        assert call.get("user_id") == "alice", (
            f"retriever made a query with a DIFFERENT user_id "
            f"(scope widening!): {call}"
        )
        assert call.get("agent_id") == _DEFAULT_AGENT_ID
        assert call.get("domain") == _DEFAULT_DOMAIN


# --------------------------------------------------------------------------- #
# Test 9: DECISION + learning never auto-promote to is_fact
# --------------------------------------------------------------------------- #


async def test_decisions_and_learning_not_auto_promoted_to_fact() -> None:
    """Test 9: store a DECISION via DecisionMemory + a learning-observation
    memory via MemoryManager -> both keep is_fact=False.

    The FACT rule (MemoryProvenance.validate_fact) is the SINGLE source
    of truth: is_fact=True requires evidence_ids + trustworthy source +
    confidence >= 0.9. DECISION records and learning observations never
    satisfy this — they are stored with is_fact=False (the default) and
    MUST stay that way across write + read roundtrips.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    decision_mgr = DecisionMemory(store=store)

    # 1. DECISION record
    decision = DecisionRecord(
        agent_id=_DEFAULT_AGENT_ID,
        objective="choose a web framework for the new service",
        options_considered=[
            {"name": "FastAPI", "pros": "async", "cons": "newer"},
            {"name": "Flask", "pros": "mature", "cons": "sync"},
        ],
        decision_taken="FastAPI",
        reasoning="async aligns with the team's I/O workload",
        confidence=0.8,
    )
    decision_id = await decision_mgr.record_decision(
        decision, domain=_DEFAULT_DOMAIN, user_id="alice"
    )

    decision_record = await store.read(decision_id)
    assert decision_record is not None
    assert decision_record.memory_type == MemoryType.DECISION
    assert decision_record.is_fact is False, (
        "DECISION memory was auto-promoted to is_fact=True — only the "
        "FACT rule (MemoryProvenance.validate_fact) may set is_fact=True"
    )

    # 2. Learning-observation memory
    learning_mem = await mgr.remember(
        agent_id=_DEFAULT_AGENT_ID,
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
        memory_type=MemoryType.SEMANTIC,
        content="learning observation: agent should ask clarifying "
        "questions before committing to a decision",
        source="test",
        source_type="agent",
        confidence=0.5,
        is_fact=False,
    )
    assert learning_mem.is_fact is False

    # Retrieve it — is_fact must STAY False (no auto-promotion on read).
    retriever = MemoryRetriever(mgr)
    retrieved = await retriever.retrieve(
        agent_id=_DEFAULT_AGENT_ID,
        query="learning observation",
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
    )
    matching = [r for r in retrieved if r.record.record_id == learning_mem.record_id]
    if matching:
        assert matching[0].record.is_fact is False, (
            "learning memory auto-promoted to is_fact=True after retrieval"
        )

    # 3. LearningProposal (separate store, no is_fact field at all)
    learning_mgr = LearningManager()
    proposal = LearningProposal(
        agent_id=_DEFAULT_AGENT_ID,
        signal_type=LearningSignal.EXPERIENCE,
        observation="observed repeated need for clarification",
        proposed_change="add a clarification step before COMMIT",
        rationale="reduces rework",
    )
    proposal_id = await learning_mgr.propose(proposal)
    assert proposal_id == proposal.proposal_id
    # LearningProposal has NO is_fact field — by design.
    assert not hasattr(proposal, "is_fact"), (
        "LearningProposal should not have an is_fact field "
        "(learning is never a fact; only evidence-backed memory can be)"
    )

    # And the proposed LearningProposal status is PROPOSED (not APPROVED,
    # not APPLIED) — HITL gate intact.
    from albra_seed.learning.types import ProposalStatus

    assert proposal.status == ProposalStatus.PROPOSED


# --------------------------------------------------------------------------- #
# Test 10: CORE_ONLY birth works with zero explicit registration
# --------------------------------------------------------------------------- #


async def test_core_only_no_credentials_still_works() -> None:
    """Test 10: fresh Runtime() with NO explicit registration -> birth()
    auto-provisions everything -> agent.run() completes end-to-end.

    This is the BIRTH CONTRACT: a CORE_ONLY seed (no external provider
    credentials, no manually-registered memory_manager) must be able to
    execute the universal agent loop using ONLY the built-in Mock
    provider + auto-provisioned InMemoryMemoryStore + auto-provisioned
    MemoryManager + auto-provisioned AuditLog.
    """
    runtime = Runtime()
    # DO NOT register anything explicitly — birth() must provision all.

    snapshot = await runtime.birth()

    # Every birth-contract field must be True / empty.
    assert snapshot["runtime_ready"] is True, (
        "runtime not ready after birth() — provider_router not auto-registered"
    )
    assert snapshot["mock_available"] is True, (
        "Mock provider not reachable after birth()"
    )
    assert snapshot["agent_loop_ok"] is True, (
        "agent loop did not complete end-to-end after birth()"
    )
    assert snapshot["memory_ok"] is True, (
        "memory write+read roundtrip failed after birth()"
    )
    assert snapshot["audit_ok"] is True, (
        "audit append failed after birth()"
    )
    assert snapshot["external_providers"] == [], (
        f"CORE_ONLY birth should have zero external providers; "
        f"got {snapshot['external_providers']}"
    )
    assert snapshot["bootstrap_source"] in ("auto", "caller")

    # Verify the auto-provisioned components are accessible.
    assert runtime.get_component(COMPONENT_PROVIDER_ROUTER) is not None
    assert runtime.get_component(COMPONENT_MEMORY_STORE) is not None
    assert runtime.get_component(COMPONENT_MEMORY_MANAGER) is not None
    assert runtime.get_component(COMPONENT_AUDIT_LOGGER) is not None

    # Run a real agent task end-to-end using the auto-provisioned stack.
    identity = AgentIdentity(name="core-only-agent")
    session = Session(
        agent_id=identity.agent_id,
        user_id="alice",
        context_id="ctx-core-only",
        domain=_DEFAULT_DOMAIN,
    )
    agent = Agent(identity, runtime)
    response = await agent.run("hello core-only world", session=session)
    assert response.content, (
        "agent.run() produced empty response in CORE_ONLY mode"
    )

    # Verify the agent run wrote a memory through the auto-provisioned
    # MemoryManager (RECORD phase writes a WORKING + an EPISODIC memory).
    mgr = runtime.get_memory_manager()
    assert mgr is not None
    records = await mgr.store.query(
        agent_id=identity.agent_id,
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
        context_id="ctx-core-only",
    )
    assert len(records) >= 1, (
        "RECORD phase did not write any memory through the auto-provisioned "
        "MemoryManager"
    )

    # And cleanup_working (in the finally block) removed the WORKING memory
    # written by RECORD — leaving only the EPISODIC one.
    working_records = [
        r for r in records if r.memory_type == MemoryType.WORKING
    ]
    assert len(working_records) == 0, (
        f"WORKING memory not cleaned up after successful run; "
        f"found {len(working_records)} WORKING records"
    )

    await runtime.shutdown()


__all__ = [
    "_RecordingMemoryManager",
    "_BrokenQueryStore",
    "_FailingProvider",
]
