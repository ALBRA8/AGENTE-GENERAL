"""Unit tests for the Universal Feedback subpackage.

Covers:
  * FeedbackRecord defaults (id prefix, timestamp, empty defaults)
  * FeedbackManager.record + get round-trip
  * FeedbackManager.query by agent_id
  * FeedbackManager.query by feedback_type
  * FeedbackManager.summary counts by type
  * Auto-correlation from CorrelationTracker context
  * link_to_decision
  * SQLiteFeedbackStore persistence (file-based)
  * All six FeedbackType values are distinct and round-trip
  * Feedback ids / store are distinct from Evidence ids / store
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from albra_seed.context.correlation import CorrelationTracker
from albra_seed.evidence import EvidenceManager, InMemoryEvidenceStore
from albra_seed.feedback import (
    FeedbackManager,
    FeedbackRecord,
    FeedbackType,
    InMemoryFeedbackStore,
    SQLiteFeedbackStore,
)
from albra_seed.provenance.tracker import ProvenanceTracker


def _run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# 1. FeedbackRecord defaults
# ----------------------------------------------------------------------


def test_feedback_record_defaults():
    rec = FeedbackRecord(
        agent_id="agent-1",
        evaluation="ok",
        feedback_type=FeedbackType.POSITIVE,
        source="evaluator-1",
        source_type="automated",
    )
    assert rec.feedback_id.startswith("fb_")
    assert rec.timestamp is not None
    assert rec.evidence_ids == []
    assert rec.metadata == {}
    assert rec.confidence == 0.0
    assert rec.execution_id is None
    assert rec.correlation_id is None
    assert rec.decision_id is None
    assert rec.skill_id is None
    assert rec.provenance_id is None
    assert rec.task_id is None
    assert rec.context_id is None
    assert rec.expected_outcome is None
    assert rec.actual_outcome is None


# ----------------------------------------------------------------------
# 2. record + get
# ----------------------------------------------------------------------


def test_feedback_manager_record_and_get():
    mgr = FeedbackManager(store=InMemoryFeedbackStore())
    fb_id = _run(
        mgr.record(
            agent_id="agent-1",
            feedback_type=FeedbackType.NEGATIVE,
            evaluation="output was wrong",
            source="human-1",
            source_type="human",
            expected="A",
            actual="B",
            confidence=0.8,
        )
    )
    assert fb_id.startswith("fb_")
    fetched = _run(mgr.get(fb_id))
    assert fetched is not None
    assert fetched.agent_id == "agent-1"
    assert fetched.feedback_type == FeedbackType.NEGATIVE
    assert fetched.evaluation == "output was wrong"
    assert fetched.expected_outcome == "A"
    assert fetched.actual_outcome == "B"
    assert fetched.confidence == pytest.approx(0.8)


# ----------------------------------------------------------------------
# 3. query by agent_id
# ----------------------------------------------------------------------


def test_feedback_manager_query_by_agent():
    mgr = FeedbackManager(store=InMemoryFeedbackStore())
    _run(
        mgr.record(
            agent_id="agent-A",
            feedback_type=FeedbackType.POSITIVE,
            evaluation="ok",
            source="s",
            source_type="automated",
        )
    )
    _run(
        mgr.record(
            agent_id="agent-B",
            feedback_type=FeedbackType.NEGATIVE,
            evaluation="bad",
            source="s",
            source_type="automated",
        )
    )
    _run(
        mgr.record(
            agent_id="agent-A",
            feedback_type=FeedbackType.CORRECTION,
            evaluation="fix",
            source="s",
            source_type="human",
        )
    )
    a_records = _run(mgr.query(agent_id="agent-A"))
    b_records = _run(mgr.query(agent_id="agent-B"))
    assert len(a_records) == 2
    assert len(b_records) == 1
    assert all(r.agent_id == "agent-A" for r in a_records)


# ----------------------------------------------------------------------
# 4. query by feedback_type
# ----------------------------------------------------------------------


def test_feedback_manager_query_by_type():
    mgr = FeedbackManager(store=InMemoryFeedbackStore())
    for _ in range(3):
        _run(
            mgr.record(
                agent_id="agent-X",
                feedback_type=FeedbackType.POSITIVE,
                evaluation="good",
                source="s",
                source_type="automated",
            )
        )
    _run(
        mgr.record(
            agent_id="agent-X",
            feedback_type=FeedbackType.NEGATIVE,
            evaluation="bad",
            source="s",
            source_type="automated",
        )
    )
    pos = _run(mgr.query(feedback_type=FeedbackType.POSITIVE))
    neg = _run(mgr.query(feedback_type=FeedbackType.NEGATIVE))
    assert len(pos) == 3
    assert len(neg) == 1
    assert all(r.feedback_type == FeedbackType.POSITIVE for r in pos)


# ----------------------------------------------------------------------
# 5. summary counts by type
# ----------------------------------------------------------------------


def test_feedback_manager_summary_counts_by_type():
    mgr = FeedbackManager(store=InMemoryFeedbackStore())
    _run(
        mgr.record(
            agent_id="agent-S",
            feedback_type=FeedbackType.POSITIVE,
            evaluation="a",
            source="s",
            source_type="automated",
        )
    )
    _run(
        mgr.record(
            agent_id="agent-S",
            feedback_type=FeedbackType.POSITIVE,
            evaluation="b",
            source="s",
            source_type="automated",
        )
    )
    _run(
        mgr.record(
            agent_id="agent-S",
            feedback_type=FeedbackType.NEGATIVE,
            evaluation="c",
            source="s",
            source_type="automated",
        )
    )
    summary = _run(mgr.summary(agent_id="agent-S"))
    # All six types present in the result, with zero for empty types.
    assert set(summary.keys()) == set(FeedbackType)
    assert summary[FeedbackType.POSITIVE] == 2
    assert summary[FeedbackType.NEGATIVE] == 1
    assert summary[FeedbackType.CORRECTION] == 0
    assert summary[FeedbackType.HUMAN_FEEDBACK] == 0
    assert summary[FeedbackType.SYSTEM_FEEDBACK] == 0
    assert summary[FeedbackType.AUTOMATED_EVALUATION] == 0


# ----------------------------------------------------------------------
# 6. auto-correlation from context
# ----------------------------------------------------------------------


def test_feedback_manager_auto_correlates_from_context():
    mgr = FeedbackManager(store=InMemoryFeedbackStore())
    with CorrelationTracker.scope(
        correlation_id="corr-xyz",
        execution_id="exec-xyz",
        agent_id="agent-ctx",
    ):
        fb_id = _run(
            mgr.record(
                agent_id="agent-ctx",
                feedback_type=FeedbackType.SYSTEM_FEEDBACK,
                evaluation="auto-correlated",
                source="runtime",
                source_type="system",
            )
        )
    fetched = _run(mgr.get(fb_id))
    assert fetched is not None
    assert fetched.correlation_id == "corr-xyz"
    assert fetched.execution_id == "exec-xyz"


# ----------------------------------------------------------------------
# 7. link_to_decision
# ----------------------------------------------------------------------


def test_feedback_manager_links_to_decision():
    mgr = FeedbackManager(store=InMemoryFeedbackStore())
    fb_id = _run(
        mgr.record(
            agent_id="agent-D",
            feedback_type=FeedbackType.CORRECTION,
            evaluation="revise",
            source="s",
            source_type="human",
        )
    )
    fetched = _run(mgr.get(fb_id))
    assert fetched is not None
    assert fetched.decision_id is None

    ok = _run(mgr.link_to_decision(fb_id, "decision-42"))
    assert ok is True

    updated = _run(mgr.get(fb_id))
    assert updated is not None
    assert updated.decision_id == "decision-42"

    # Non-existent id returns False.
    miss = _run(mgr.link_to_decision("fb_does-not-exist", "decision-X"))
    assert miss is False


# ----------------------------------------------------------------------
# 8. SQLite store persistence
# ----------------------------------------------------------------------


def test_sqlite_feedback_store_persists():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "feedback.sqlite")
        # Write with one store instance.
        store1 = SQLiteFeedbackStore(db_path=path)
        mgr1 = FeedbackManager(store=store1)
        fb_id = _run(
            mgr1.record(
                agent_id="agent-SQL",
                feedback_type=FeedbackType.AUTOMATED_EVALUATION,
                evaluation="score=0.87",
                source="judge",
                source_type="automated",
                confidence=0.87,
                evidence_ids=["ev_1", "ev_2"],
            )
        )
        store1.close()
        # Reopen from the same file with a new instance.
        store2 = SQLiteFeedbackStore(db_path=path)
        mgr2 = FeedbackManager(store=store2)
        fetched = _run(mgr2.get(fb_id))
        assert fetched is not None
        assert fetched.agent_id == "agent-SQL"
        assert fetched.feedback_type == FeedbackType.AUTOMATED_EVALUATION
        assert fetched.confidence == pytest.approx(0.87)
        assert fetched.evidence_ids == ["ev_1", "ev_2"]
        store2.close()


# ----------------------------------------------------------------------
# 9. all six feedback types are distinct and round-trip
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "ftype",
    list(FeedbackType),
)
def test_feedback_distinguishes_all_six_types(ftype: FeedbackType):
    mgr = FeedbackManager(store=InMemoryFeedbackStore())
    fb_id = _run(
        mgr.record(
            agent_id=f"agent-{ftype.value}",
            feedback_type=ftype,
            evaluation=f"type-{ftype.value}",
            source="s",
            source_type="automated",
        )
    )
    fetched = _run(mgr.get(fb_id))
    assert fetched is not None
    assert fetched.feedback_type == ftype
    assert fetched.feedback_type.value == ftype.value
    # All six values are distinct strings.
    all_values = {t.value for t in FeedbackType}
    assert len(all_values) == 6
    assert ftype.value in all_values


# ----------------------------------------------------------------------
# 10. feedback never confused with evidence
# ----------------------------------------------------------------------


def test_feedback_never_confused_with_evidence():
    fb_mgr = FeedbackManager(store=InMemoryFeedbackStore())
    ev_mgr = EvidenceManager(store=InMemoryEvidenceStore())

    fb_id = _run(
        fb_mgr.record(
            agent_id="agent-1",
            feedback_type=FeedbackType.POSITIVE,
            evaluation="ok",
            source="s",
            source_type="automated",
        )
    )
    ev = _run(
        ev_mgr.register(
            source="tool:x",
            source_type="tool",
            content={"k": "v"},
            confidence=0.95,
        )
    )

    # Distinct id prefixes.
    assert fb_id.startswith("fb_")
    assert ev.evidence_id.startswith("ev_")
    assert not fb_id.startswith("ev_")
    assert not ev.evidence_id.startswith("fb_")

    # Feedback store does NOT return evidence ids and vice versa.
    assert _run(fb_mgr.get(ev.evidence_id)) is None
    assert _run(ev_mgr.retrieve(fb_id)) is None

    # Counts: each store counts only its own records.
    assert _run(fb_mgr.count()) == 1
    assert _run(ev_mgr.list_all()) == [ev] or len(_run(ev_mgr.list_all())) == 1


# ----------------------------------------------------------------------
# 11. (bonus) provenance is best-effort attached
# ----------------------------------------------------------------------


def test_feedback_manager_attaches_provenance_best_effort():
    tracker = ProvenanceTracker()
    mgr = FeedbackManager(
        store=InMemoryFeedbackStore(),
        provenance_tracker=tracker,
    )
    fb_id = _run(
        mgr.record(
            agent_id="agent-P",
            feedback_type=FeedbackType.POSITIVE,
            evaluation="good",
            source="s",
            source_type="automated",
            evidence_ids=["ev_a", "ev_b"],
            confidence=0.9,
        )
    )
    fetched = _run(mgr.get(fb_id))
    assert fetched is not None
    # Provenance attached.
    assert fetched.provenance_id is not None
    assert fetched.provenance_id.startswith("prov_")
    # Cross-check via the tracker.
    provs = _run(tracker.for_record("feedback", fb_id))
    assert len(provs) == 1
    assert provs[0].evidence_ids == ["ev_a", "ev_b"]


def test_feedback_manager_without_tracker_still_works():
    mgr = FeedbackManager(store=InMemoryFeedbackStore())
    # No provenance tracker configured; record must still succeed.
    fb_id = _run(
        mgr.record(
            agent_id="agent-NP",
            feedback_type=FeedbackType.POSITIVE,
            evaluation="ok",
            source="s",
            source_type="automated",
        )
    )
    fetched = _run(mgr.get(fb_id))
    assert fetched is not None
    assert fetched.provenance_id is None
