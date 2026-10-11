"""Unit tests for the memory subpackage (MemoryDV).

Covers in-memory + SQLite stores, isolator, consolidation, provenance,
manager remember/recall, and cross-agent isolation.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from albra_seed.core.types import MemoryType
from albra_seed.memory import (
    ConsolidationPolicy,
    InMemoryMemoryStore,
    MemoryIsolator,
    MemoryManager,
    MemoryProvenance,
    SQLiteMemoryStore,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_inmemory_store_write_and_read():
    store = InMemoryMemoryStore()
    from albra_seed.core.base import MemoryRecord

    record = MemoryRecord(
        record_id="mem-1",
        agent_id="agent-A",
        domain="default",
        user_id="user-1",
        context_id="ctx-1",
        memory_type=MemoryType.EPISODIC,
        content="hello",
        confidence=0.5,
    )

    rid = _run(store.write(record))
    assert rid == "mem-1"
    got = _run(store.read("mem-1"))
    assert got is not None
    assert got.content == "hello"
    assert got.agent_id == "agent-A"

    results = _run(
        store.query(
            agent_id="agent-A",
            domain="default",
            user_id="user-1",
            context_id="ctx-1",
        )
    )
    assert len(results) == 1
    assert results[0].record_id == "mem-1"


def test_sqlite_store_write_and_read():
    from albra_seed.core.base import MemoryRecord

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mem.db")
        store = SQLiteMemoryStore(db_path=path)
        record = MemoryRecord(
            record_id="mem-sql-1",
            agent_id="agent-B",
            domain="legal",
            user_id="u2",
            context_id="c2",
            memory_type=MemoryType.SEMANTIC,
            content={"k": "v"},
            confidence=0.8,
        )
        _run(store.write(record))

        # reopen to verify persistence
        store.close()
        store2 = SQLiteMemoryStore(db_path=path)
        got = _run(store2.read("mem-sql-1"))
        assert got is not None
        assert got.agent_id == "agent-B"
        assert got.memory_type == MemoryType.SEMANTIC
        assert got.confidence == pytest.approx(0.8)
        store2.close()


def test_memory_isolator_keys_correctly():
    k1 = MemoryIsolator.key_for("agent-A", "default", "user-1", "ctx-1")
    k2 = MemoryIsolator.key_for("agent-A", "default", "user-1", "ctx-1")
    k3 = MemoryIsolator.key_for("agent-A", "default", "user-2", "ctx-1")
    k4 = MemoryIsolator.key_for("agent-B", "default", "user-1", "ctx-1")
    assert k1 == k2
    assert k1 != k3
    assert k1 != k4
    assert str(k1) == "mem::agent-A::default::user-1::ctx-1"

    # empty agent_id must raise
    with pytest.raises(ValueError):
        MemoryIsolator.key_for("", "default")


def test_consolidation_promotes_episodic_to_semantic():
    from albra_seed.core.base import MemoryRecord

    policy = ConsolidationPolicy(confidence_threshold=0.7)
    record = MemoryRecord(
        record_id="mem-epi-1",
        agent_id="agent-A",
        memory_type=MemoryType.EPISODIC,
        content="event X happened",
        confidence=0.9,
    )
    result = policy.consolidate(record)
    assert result.promoted is True
    assert result.new_type == MemoryType.SEMANTIC
    assert record.memory_type == MemoryType.SEMANTIC
    assert record.version == 2


def test_consolidation_never_promotes_unknown_to_fact():
    from albra_seed.core.base import MemoryRecord

    policy = ConsolidationPolicy(confidence_threshold=0.7)
    record = MemoryRecord(
        record_id="mem-unk-1",
        agent_id="agent-A",
        memory_type=MemoryType.EPISODIC,
        content="maybe X",
        confidence=0.95,
        is_fact=False,
    )
    result = policy.consolidate(record)
    # Episodic with high confidence promotes to SEMANTIC, not FACT
    assert result.promoted is True
    assert result.new_type == MemoryType.SEMANTIC
    assert record.is_fact is False  # consolidation NEVER sets is_fact
    assert policy.can_promote_to_fact(record) is False  # contract hard gate


def test_memory_provenance_attached():
    from albra_seed.core.base import MemoryRecord

    prov_builder = MemoryProvenance()
    record = MemoryRecord(
        record_id="mem-prov-1",
        agent_id="agent-A",
        memory_type=MemoryType.FACTUAL,
        content="fact X",
        confidence=0.92,
        evidence_ids=["ev-1", "ev-2"],
        source="tool",
        source_type="tool",
    )
    record, prov = prov_builder.attach(record)
    assert prov.provenance_id is not None
    assert record.provenance_id == prov.provenance_id
    assert prov.evidence_ids == ["ev-1", "ev-2"]
    assert prov.confidence == pytest.approx(0.92)
    assert prov.source == "tool"
    assert prov.source_type == "tool"
    assert prov.version == 1
    assert prov.modified_at is None
    assert record.modified_at is not None

    # fact requires evidence gate
    assert MemoryProvenance.requires_evidence_for_fact(record) is True
    record_no_ev = record.model_copy(update={"is_fact": True, "evidence_ids": []})
    assert MemoryProvenance.requires_evidence_for_fact(record_no_ev) is False


def test_memory_manager_remember_and_recall():
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    rec = _run(
        mgr.remember(
            agent_id="agent-A",
            content="user prefers concise answers",
            memory_type=MemoryType.SEMANTIC,
            domain="default",
            user_id="user-1",
            context_id="ctx-1",
            confidence=0.6,
            source="agent",
            source_type="agent",
            evidence_ids=["ev-1"],
        )
    )
    assert rec.record_id is not None
    assert rec.provenance_id is not None

    results = _run(
        mgr.recall(
            agent_id="agent-A",
            domain="default",
            user_id="user-1",
            context_id="ctx-1",
        )
    )
    assert len(results) == 1
    assert results[0].record_id == rec.record_id

    # forgot
    ok = _run(mgr.forget(rec.record_id, reason="test"))
    assert ok is True
    after = _run(mgr.recall(agent_id="agent-A", user_id="user-1", context_id="ctx-1"))
    assert after == []


def test_memory_isolation_between_agents():
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    _run(
        mgr.remember(
            agent_id="agent-A",
            content="A's memory",
            memory_type=MemoryType.SEMANTIC,
            user_id="user-1",
            context_id="ctx-1",
        )
    )
    _run(
        mgr.remember(
            agent_id="agent-B",
            content="B's memory",
            memory_type=MemoryType.SEMANTIC,
            user_id="user-1",
            context_id="ctx-1",
        )
    )

    a = _run(mgr.recall(agent_id="agent-A", user_id="user-1", context_id="ctx-1"))
    b = _run(mgr.recall(agent_id="agent-B", user_id="user-1", context_id="ctx-1"))

    assert len(a) == 1
    assert len(b) == 1
    assert a[0].agent_id == "agent-A"
    assert b[0].agent_id == "agent-B"
    assert a[0].content == "A's memory"
    assert b[0].content == "B's memory"

    # also: same agent, different user
    _run(
        mgr.remember(
            agent_id="agent-A",
            content="A's user-2 memory",
            memory_type=MemoryType.SEMANTIC,
            user_id="user-2",
            context_id="ctx-1",
        )
    )
    a_u1 = _run(mgr.recall(agent_id="agent-A", user_id="user-1", context_id="ctx-1"))
    a_u2 = _run(mgr.recall(agent_id="agent-A", user_id="user-2", context_id="ctx-1"))
    assert len(a_u1) == 1
    assert len(a_u2) == 1
    assert a_u1[0].content == "A's memory"
    assert a_u2[0].content == "A's user-2 memory"


def test_memory_manager_rejects_fact_without_evidence():
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    with pytest.raises(ValueError):
        _run(
            mgr.remember(
                agent_id="agent-A",
                content="claimed fact",
                memory_type=MemoryType.FACTUAL,
                is_fact=True,
                # no evidence_ids
            )
        )
