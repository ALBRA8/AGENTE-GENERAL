"""BLOQ 4 — Memory Factual Integrity tests.

These tests verify the UNIVERSAL FACT rule is enforced consistently through
`MemoryProvenance.validate_fact` — the single source of truth — and that
`MemoryManager.remember` delegates to it rather than re-implementing the
check inline.

The rule (defined in `FactRule`, returned by `MemoryProvenance.fact_rule()`):
    - require_evidence: at least one evidence_id
    - source_type in {"tool", "external", "verified_provider"}
    - confidence >= 0.9
"""

from __future__ import annotations

import os
import tempfile

import pytest

from albra_seed.core.base import MemoryRecord
from albra_seed.core.types import MemoryType
from albra_seed.memory import (
    ConsolidationPolicy,
    InMemoryMemoryStore,
    MemoryManager,
    MemoryProvenance,
    SQLiteMemoryStore,
)
from albra_seed.memory.provenance import FactRule


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _run(coro):
    return asyncio.run(coro)


import asyncio  # noqa: E402  (kept after helpers to mirror test_memory.py)


# --------------------------------------------------------------------------- #
# FactRule model + fact_rule() — single source of truth
# --------------------------------------------------------------------------- #


def test_fact_rule_defaults():
    rule = MemoryProvenance.fact_rule()
    assert isinstance(rule, FactRule)
    assert rule.require_evidence is True
    assert rule.allowed_source_types == frozenset(
        {"tool", "external", "verified_provider"}
    )
    assert rule.min_confidence == pytest.approx(0.9)


def test_validate_fact_non_fact_skips_rule():
    """A non-fact record (is_fact=False) is always valid against the FACT rule."""
    record = MemoryRecord(
        agent_id="agent-A",
        memory_type=MemoryType.EPISODIC,
        content="not a fact",
        confidence=0.1,
        source_type="agent",
        evidence_ids=[],
    )
    is_valid, why = MemoryProvenance.validate_fact(record)
    assert is_valid is True
    assert why is None


# --------------------------------------------------------------------------- #
# BLOQ 4 — the 5 manager-level tests + 2 cross-store tests
# --------------------------------------------------------------------------- #


def test_fact_without_evidence_raises():
    """`remember(is_fact=True, evidence_ids=[])` MUST raise ValueError."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    with pytest.raises(ValueError, match="evidence"):
        _run(
            mgr.remember(
                agent_id="agent-A",
                content="claimed fact with no backing",
                memory_type=MemoryType.FACTUAL,
                is_fact=True,
                evidence_ids=[],
                source_type="tool",
                confidence=0.95,
            )
        )


def test_fact_with_evidence_allowed():
    """`remember(is_fact=True, evidence_ids=[...], source_type='tool',
    confidence=0.95)` MUST succeed."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    rec = _run(
        mgr.remember(
            agent_id="agent-A",
            content="verified fact",
            memory_type=MemoryType.FACTUAL,
            is_fact=True,
            evidence_ids=["ev_1"],
            source_type="tool",
            confidence=0.95,
        )
    )
    assert rec.is_fact is True
    assert rec.evidence_ids == ["ev_1"]
    # And it must be retrievable.
    fetched = _run(mgr.get(rec.record_id))
    assert fetched is not None
    assert fetched.is_fact is True


def test_fact_with_invalid_source_type_rejected():
    """`source_type='agent'` is NOT in the allowed set — must raise."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    with pytest.raises(ValueError, match="source_type"):
        _run(
            mgr.remember(
                agent_id="agent-A",
                content="agent-claimed fact",
                memory_type=MemoryType.FACTUAL,
                is_fact=True,
                evidence_ids=["ev_1"],
                source_type="agent",
                confidence=0.95,
            )
        )


def test_fact_with_low_confidence_rejected():
    """confidence 0.5 < 0.9 threshold — must raise."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    with pytest.raises(ValueError, match="confidence"):
        _run(
            mgr.remember(
                agent_id="agent-A",
                content="low-confidence claim",
                memory_type=MemoryType.FACTUAL,
                is_fact=True,
                evidence_ids=["ev_1"],
                source_type="tool",
                confidence=0.5,
            )
        )


def test_non_fact_continues_working():
    """Non-fact records bypass the FACT rule entirely (default params)."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    rec = _run(
        mgr.remember(
            agent_id="agent-A",
            content="just an episodic observation",
            memory_type=MemoryType.EPISODIC,
            # is_fact defaults to False; no evidence, source=agent, conf=0.0
        )
    )
    assert rec.is_fact is False
    assert rec.evidence_ids == []


def test_consolidation_respects_fact_rule():
    """If a caller hand-edits a record to is_fact=True without meeting the
    rule, consolidate() must reject the promotion and leave the record
    unchanged in the store."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store, consolidation=ConsolidationPolicy(confidence_threshold=0.5))
    # Write a valid episodic record first.
    rec = _run(
        mgr.remember(
            agent_id="agent-A",
            content="episodic that should consolidate",
            memory_type=MemoryType.EPISODIC,
            confidence=0.8,
        )
    )
    # Now tamper: flip is_fact=True with no evidence / bad source.
    # `consolidate()` reads from the store, so we mutate the stored record.
    stored = _run(mgr.get(rec.record_id))
    assert stored is not None
    stored.is_fact = True
    stored.source_type = "agent"  # not in allowed set
    _run(store.write(stored))

    result = _run(mgr.consolidate(rec.record_id))
    assert result.promoted is False
    assert "FACT rule" in result.reason or "fact" in result.reason.lower()

    # The record in the store should still be EPISODIC (promotion rejected).
    after = _run(mgr.get(rec.record_id))
    assert after is not None
    assert after.memory_type == MemoryType.EPISODIC


def test_persistence_respects_fact_rule():
    """Writing a fact record to SQLite via `remember` must still apply the
    rule — nothing lands in the DB if validation fails."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mem.db")
        store = SQLiteMemoryStore(db_path=path)
        mgr = MemoryManager(store=store)
        # Invalid: fact without evidence
        with pytest.raises(ValueError, match="evidence"):
            _run(
                mgr.remember(
                    agent_id="agent-A",
                    content="invalid fact",
                    memory_type=MemoryType.FACTUAL,
                    is_fact=True,
                    evidence_ids=[],
                    source_type="tool",
                    confidence=0.95,
                )
            )
        # Invalid: fact with bad source_type
        with pytest.raises(ValueError, match="source_type"):
            _run(
                mgr.remember(
                    agent_id="agent-A",
                    content="invalid source",
                    memory_type=MemoryType.FACTUAL,
                    is_fact=True,
                    evidence_ids=["ev_1"],
                    source_type="agent",
                    confidence=0.95,
                )
            )
        # Invalid: fact with low confidence
        with pytest.raises(ValueError, match="confidence"):
            _run(
                mgr.remember(
                    agent_id="agent-A",
                    content="low conf",
                    memory_type=MemoryType.FACTUAL,
                    is_fact=True,
                    evidence_ids=["ev_1"],
                    source_type="tool",
                    confidence=0.3,
                )
            )
        # Nothing should be in the DB.
        assert _run(store.count(agent_id="agent-A")) == 0
        store.close()

        # Reopen — still empty.
        store2 = SQLiteMemoryStore(db_path=path)
        assert _run(store2.count(agent_id="agent-A")) == 0

        # A VALID fact must round-trip.
        mgr2 = MemoryManager(store=store2)
        rec = _run(
            mgr2.remember(
                agent_id="agent-A",
                content="valid fact",
                memory_type=MemoryType.FACTUAL,
                is_fact=True,
                evidence_ids=["ev_1", "ev_2"],
                source_type="external",
                confidence=0.99,
            )
        )
        assert rec.is_fact is True
        store2.close()

        store3 = SQLiteMemoryStore(db_path=path)
        fetched = _run(store3.read(rec.record_id))
        assert fetched is not None
        assert fetched.is_fact is True
        assert fetched.evidence_ids == ["ev_1", "ev_2"]
        assert fetched.source_type == "external"
        store3.close()


def test_validate_fact_returns_descriptive_reason_for_each_violation():
    """The reason string must explain WHICH rule was violated."""
    # No evidence
    r1 = MemoryRecord(
        agent_id="a",
        memory_type=MemoryType.FACTUAL,
        content="x",
        is_fact=True,
        evidence_ids=[],
        source_type="tool",
        confidence=0.95,
    )
    ok1, why1 = MemoryProvenance.validate_fact(r1)
    assert ok1 is False
    assert "evidence" in why1

    # Bad source_type
    r2 = MemoryRecord(
        agent_id="a",
        memory_type=MemoryType.FACTUAL,
        content="x",
        is_fact=True,
        evidence_ids=["ev_1"],
        source_type="agent",
        confidence=0.95,
    )
    ok2, why2 = MemoryProvenance.validate_fact(r2)
    assert ok2 is False
    assert "source_type" in why2

    # Low confidence
    r3 = MemoryRecord(
        agent_id="a",
        memory_type=MemoryType.FACTUAL,
        content="x",
        is_fact=True,
        evidence_ids=["ev_1"],
        source_type="verified_provider",
        confidence=0.7,
    )
    ok3, why3 = MemoryProvenance.validate_fact(r3)
    assert ok3 is False
    assert "confidence" in why3


def test_all_allowed_source_types_pass():
    """Every source_type in the allowed set must pass (with evidence + conf)."""
    for st in ("tool", "external", "verified_provider"):
        r = MemoryRecord(
            agent_id="a",
            memory_type=MemoryType.FACTUAL,
            content="x",
            is_fact=True,
            evidence_ids=["ev_1"],
            source_type=st,
            confidence=0.9,
        )
        ok, why = MemoryProvenance.validate_fact(r)
        assert ok is True, f"source_type={st!r} should pass but got: {why}"
