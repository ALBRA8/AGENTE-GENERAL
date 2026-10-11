"""Unit tests for Decision Memory (GAP-3, non-destructive extension).

These tests exercise DecisionRecord (the structured wrapper) and
DecisionMemory (the thin adapter over MemoryStore). They verify that:

    1. DecisionRecord <-> MemoryRecord conversion is lossless.
    2. DecisionMemory stores decisions inside an existing MemoryStore.
    3. list_decisions filters by agent_id (isolation by agent).
    4. list_decisions is isolated by domain (MemoryDV rule preserved).
    5. link_outcome updates the underlying MemoryRecord in place.
    6. Decisions are stored as DECISION memory_type and do NOT collide
       with EPISODIC/SEMANTIC/etc. (isolation by memory_type).
    7. When evidence_ids are provided, ProvenanceTracker records a
       `decision` provenance entry pointing at the decision record.
"""

from __future__ import annotations

import asyncio

import pytest

from albra_seed.core.base import MemoryRecord
from albra_seed.core.types import MemoryType
from albra_seed.memory import (
    DecisionMemory,
    DecisionRecord,
    InMemoryMemoryStore,
    MemoryManager,
)
from albra_seed.provenance.tracker import ProvenanceTracker


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_decision(
    *,
    agent_id: str = "agent-A",
    objective: str = "Pick a provider",
    decision_taken: str = "Use Mock provider",
    evidence_ids: list[str] | None = None,
    context_id: str | None = None,
    correlation_id: str | None = None,
    execution_id: str | None = None,
) -> DecisionRecord:
    return DecisionRecord(
        agent_id=agent_id,
        context_id=context_id,
        objective=objective,
        options_considered=[
            {"label": "Mock provider", "score": 0.9},
            {"label": "OpenAI provider", "score": 0.4},
        ],
        decision_taken=decision_taken,
        reasoning="Mock provider has no external deps and is_core_only safe.",
        evidence_ids=list(evidence_ids or []),
        execution_id=execution_id,
        correlation_id=correlation_id,
        confidence=0.82,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_decision_record_converts_to_memory_record():
    decision = _make_decision(evidence_ids=["ev-1", "ev-2"])
    record = decision.to_memory_record(domain="default")

    assert record.record_id == decision.decision_id
    assert record.memory_type == MemoryType.DECISION
    assert record.agent_id == decision.agent_id
    assert record.context_id == decision.context_id
    assert record.evidence_ids == ["ev-1", "ev-2"]
    assert record.confidence == pytest.approx(0.82)
    assert isinstance(record.content, dict)
    assert record.content["decision_taken"] == "Use Mock provider"
    assert record.content["objective"] == "Pick a provider"

    # Round-trip: from_memory_record must reconstruct the same payload.
    rebuilt = DecisionRecord.from_memory_record(record)
    assert rebuilt.decision_id == decision.decision_id
    assert rebuilt.decision_taken == decision.decision_taken
    assert rebuilt.evidence_ids == decision.evidence_ids
    assert rebuilt.confidence == pytest.approx(decision.confidence)
    assert rebuilt.options_considered == decision.options_considered


def test_decision_record_rejects_non_decision_memory_record():
    # A non-DECISION MemoryRecord must NOT be coerced into a DecisionRecord.
    other = MemoryRecord(
        agent_id="agent-A",
        memory_type=MemoryType.EPISODIC,
        content={"foo": "bar"},
    )
    with pytest.raises(TypeError):
        DecisionRecord.from_memory_record(other)


def test_decision_memory_record_and_get():
    store = InMemoryMemoryStore()
    dm = DecisionMemory(store=store)

    decision = _make_decision()
    decision_id = _run(dm.record_decision(decision, domain="default"))
    assert decision_id == decision.decision_id

    fetched = _run(dm.get_decision(decision_id))
    assert fetched is not None
    assert fetched.decision_taken == "Use Mock provider"
    assert fetched.agent_id == "agent-A"
    assert fetched.confidence == pytest.approx(0.82)

    # The underlying store must hold a MemoryRecord(memory_type=DECISION).
    raw = _run(store.read(decision_id))
    assert raw is not None
    assert raw.memory_type == MemoryType.DECISION
    assert raw.provenance_id is not None  # MemoryProvenance attached

    # Unknown id returns None.
    assert _run(dm.get_decision("does-not-exist")) is None


def test_decision_memory_list_by_agent():
    store = InMemoryMemoryStore()
    dm = DecisionMemory(store=store)

    _run(dm.record_decision(_make_decision(agent_id="agent-A")))
    _run(dm.record_decision(_make_decision(agent_id="agent-A", objective="obj-2")))
    _run(dm.record_decision(_make_decision(agent_id="agent-B", objective="other")))

    a = _run(dm.list_decisions(agent_id="agent-A"))
    b = _run(dm.list_decisions(agent_id="agent-B"))
    assert len(a) == 2
    assert len(b) == 1
    assert {d.agent_id for d in a} == {"agent-A"}
    assert b[0].agent_id == "agent-B"


def test_decision_memory_isolated_by_domain():
    store = InMemoryMemoryStore()
    dm = DecisionMemory(store=store)

    _run(dm.record_decision(_make_decision(), domain="legal"))
    _run(dm.record_decision(_make_decision(), domain="sales"))
    _run(dm.record_decision(_make_decision(), domain="legal"))

    legal = _run(dm.list_decisions(agent_id="agent-A", domain="legal"))
    sales = _run(dm.list_decisions(agent_id="agent-A", domain="sales"))
    other = _run(dm.list_decisions(agent_id="agent-A", domain="default"))

    assert len(legal) == 2
    assert len(sales) == 1
    assert len(other) == 0  # nothing in "default"


def test_decision_memory_isolated_by_context_and_user():
    store = InMemoryMemoryStore()
    dm = DecisionMemory(store=store)

    _run(
        dm.record_decision(
            _make_decision(context_id="ctx-1"),
            domain="default",
            user_id="user-1",
        )
    )
    _run(
        dm.record_decision(
            _make_decision(context_id="ctx-2"),
            domain="default",
            user_id="user-1",
        )
    )
    # Same agent, different user → isolation must hide both.
    _run(
        dm.record_decision(
            _make_decision(context_id="ctx-1"),
            domain="default",
            user_id="user-2",
        )
    )

    ctx1 = _run(
        dm.list_decisions(
            agent_id="agent-A",
            domain="default",
            context_id="ctx-1",
            user_id="user-1",
        )
    )
    ctx2 = _run(
        dm.list_decisions(
            agent_id="agent-A",
            domain="default",
            context_id="ctx-2",
            user_id="user-1",
        )
    )
    assert len(ctx1) == 1
    assert len(ctx2) == 1
    assert ctx1[0].context_id == "ctx-1"
    assert ctx2[0].context_id == "ctx-2"


def test_decision_memory_link_outcome():
    store = InMemoryMemoryStore()
    dm = DecisionMemory(store=store)

    decision_id = _run(dm.record_decision(_make_decision()))
    fetched = _run(dm.get_decision(decision_id))
    assert fetched is not None
    assert fetched.outcome is None
    assert fetched.feedback_ids == []

    ok = _run(
        dm.link_outcome(
            decision_id,
            outcome="Decision was correct; user confirmed.",
            feedback_ids=["fb-1", "fb-2"],
        )
    )
    assert ok is True

    updated = _run(dm.get_decision(decision_id))
    assert updated is not None
    assert updated.outcome == "Decision was correct; user confirmed."
    assert updated.feedback_ids == ["fb-1", "fb-2"]

    # Underlying MemoryRecord version must have been bumped.
    raw = _run(store.read(decision_id))
    assert raw is not None
    assert raw.version >= 2

    # Linking again must merge, not replace.
    ok2 = _run(dm.link_outcome(decision_id, outcome="follow-up", feedback_ids=["fb-2", "fb-3"]))
    assert ok2 is True
    updated2 = _run(dm.get_decision(decision_id))
    assert updated2 is not None
    assert updated2.feedback_ids == ["fb-1", "fb-2", "fb-3"]
    assert updated2.outcome == "follow-up"

    # Unknown id → False, no exception.
    assert _run(dm.link_outcome("nope", outcome="x")) is False


def test_decision_memory_reuses_memorydv_isolation():
    """DecisionMemory must NOT introduce a parallel storage space.
    A MemoryManager pointing at the same store must see the DECISION
    records under the same isolation bucket, and a non-DECISION memory
    must NOT show up in list_decisions()."""
    store = InMemoryMemoryStore()
    dm = DecisionMemory(store=store)
    mgr = MemoryManager(store=store)

    _run(dm.record_decision(_make_decision(agent_id="agent-A")))
    _run(
        mgr.remember(
            agent_id="agent-A",
            content="user prefers concise answers",
            memory_type=MemoryType.SEMANTIC,
        )
    )

    # list_decisions only returns DECISION memories, not EPISODIC/SEMANTIC.
    decisions = _run(dm.list_decisions(agent_id="agent-A"))
    assert len(decisions) == 1
    assert decisions[0].decision_taken == "Use Mock provider"

    # But MemoryManager.recall (no memory_type filter) sees BOTH.
    all_records = _run(mgr.recall(agent_id="agent-A", limit=10))
    types = {r.memory_type for r in all_records}
    assert MemoryType.DECISION in types
    assert MemoryType.SEMANTIC in types


def test_decision_record_preserves_provenance():
    """When evidence_ids are present, ProvenanceTracker must record a
    `decision` provenance entry pointing at the decision record_id."""
    store = InMemoryMemoryStore()
    tracker = ProvenanceTracker()
    dm = DecisionMemory(store=store, tracker=tracker)

    decision = _make_decision(
        evidence_ids=["ev-1", "ev-2"],
        execution_id="exec-9",
        correlation_id="corr-9",
    )
    decision_id = _run(dm.record_decision(decision))

    # ProvenanceTracker must have recorded a decision-kind entry.
    provs = _run(tracker.for_record("decision", decision_id))
    assert len(provs) >= 1
    prov = provs[0]
    assert prov.source == decision.decision_taken  # track_decision uses decision label as source
    assert prov.source_type == "agent"
    assert set(prov.evidence_ids) == {"ev-1", "ev-2"}
    assert prov.execution_id == "exec-9"
    assert prov.correlation_id == "corr-9"

    # The MemoryRecord itself also carries MemoryProvenance (provenance_id set).
    raw = _run(store.read(decision_id))
    assert raw is not None
    assert raw.provenance_id is not None


def test_decision_memory_preserves_existing_test_suite_invariants():
    """Sanity: adding DECISION must not break the 5-type MemoryType set.
    The original 5 types must still be present and unchanged."""
    expected_original = {
        MemoryType.EPISODIC,
        MemoryType.SEMANTIC,
        MemoryType.FACTUAL,
        MemoryType.PROCEDURAL,
        MemoryType.WORKING,
    }
    assert expected_original.issubset(set(MemoryType))
    assert MemoryType.DECISION not in expected_original
    assert MemoryType.DECISION.value == "decision"
