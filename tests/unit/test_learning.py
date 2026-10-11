"""Unit tests for the Universal Learning subpackage (GAP-2).

Covers the proposal pipeline with HITL:
  * LearningProposal defaults (status=PROPOSED, id prefix, empty lists)
  * LearningManager.propose + get round-trip
  * Full lifecycle: PROPOSED → REVIEW_REQUIRED → APPROVED → APPLIED
  * reject records reason + reviewer
  * apply() on a REJECTED proposal raises (status guard)
  * apply() without prior approval raises (PROPOSED → APPLIED forbidden)
  * supersede marks old proposal SUPERSEDED, new proposal unchanged
  * ProposalGenerator.from_feedback (≥3 same-type same-target records)
  * ProposalGenerator.from_decision_outcome (outcome negative keyword)
  * correlation_id is preserved through propose()
"""

from __future__ import annotations

import asyncio

import pytest

from albra_seed.context.correlation import CorrelationTracker
from albra_seed.execution.trace import ExecutionTrace
from albra_seed.feedback import FeedbackManager, FeedbackType, InMemoryFeedbackStore
from albra_seed.learning import (
    InMemoryLearningStore,
    LearningError,
    LearningManager,
    LearningProposal,
    LearningSignal,
    ProposalGenerator,
    ProposalStatus,
)
from albra_seed.memory import DecisionMemory, DecisionRecord, InMemoryMemoryStore


def _run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------
# 1. defaults
# ----------------------------------------------------------------------


def test_learning_proposal_defaults_to_proposed_status():
    p = LearningProposal(
        agent_id="agent-1",
        signal_type=LearningSignal.FEEDBACK,
        observation="obs",
        proposed_change="change",
        rationale="why",
    )
    assert p.proposal_id.startswith("learn_")
    assert p.status == ProposalStatus.PROPOSED
    assert p.signal_type == LearningSignal.FEEDBACK
    assert p.source_feedback_ids == []
    assert p.source_decision_ids == []
    assert p.source_execution_ids == []
    assert p.evidence_ids == []
    assert p.target_component == "other"
    assert p.target_id is None
    assert p.confidence == 0.0
    assert p.execution_id is None
    assert p.correlation_id is None
    assert p.reviewed_at is None
    assert p.reviewed_by is None
    assert p.applied_at is None
    assert p.rejection_reason is None
    assert p.metadata == {}
    assert p.created_at is not None


# ----------------------------------------------------------------------
# 2. propose + get
# ----------------------------------------------------------------------


def test_learning_manager_propose_and_get():
    mgr = LearningManager(store=InMemoryLearningStore())
    p = LearningProposal(
        agent_id="agent-1",
        signal_type=LearningSignal.EXPERIENCE,
        observation="trace had errors",
        proposed_change="add retry",
        rationale="errors suggest transient failure",
        target_component="tool",
        confidence=0.7,
    )
    pid = _run(mgr.propose(p))
    assert pid.startswith("learn_")
    fetched = _run(mgr.get(pid))
    assert fetched is not None
    assert fetched.agent_id == "agent-1"
    assert fetched.status == ProposalStatus.PROPOSED
    assert fetched.signal_type == LearningSignal.EXPERIENCE
    assert fetched.target_component == "tool"
    assert fetched.confidence == pytest.approx(0.7)
    # propose() must force status to PROPOSED even if caller smuggled another
    assert fetched.reviewed_at is None
    assert fetched.applied_at is None
    assert fetched.rejection_reason is None


# ----------------------------------------------------------------------
# 3. full lifecycle
# ----------------------------------------------------------------------


def test_learning_pipeline_proposed_to_review_to_approved_to_applied():
    mgr = LearningManager(store=InMemoryLearningStore())
    p = LearningProposal(
        agent_id="agent-2",
        signal_type=LearningSignal.DECISION,
        observation="d1 wrong",
        proposed_change="adjust policy",
        rationale="decision mismatch",
        target_component="policy",
    )
    pid = _run(mgr.propose(p))

    # PROPOSED -> REVIEW_REQUIRED
    ok = _run(mgr.request_review(pid))
    assert ok is True
    rec = _run(mgr.get(pid))
    assert rec is not None and rec.status == ProposalStatus.REVIEW_REQUIRED

    # REVIEW_REQUIRED -> APPROVED
    ok = _run(mgr.approve(pid, approver="reviewer-1"))
    assert ok is True
    rec = _run(mgr.get(pid))
    assert rec is not None
    assert rec.status == ProposalStatus.APPROVED
    assert rec.reviewed_by == "reviewer-1"
    assert rec.reviewed_at is not None
    assert rec.applied_at is None  # not applied yet

    # APPROVED -> APPLIED
    ok = _run(mgr.apply(pid, applied_by="dev-ops"))
    assert ok is True
    rec = _run(mgr.get(pid))
    assert rec is not None
    assert rec.status == ProposalStatus.APPLIED
    assert rec.applied_at is not None
    assert rec.metadata.get("applied_by") == "dev-ops"


# ----------------------------------------------------------------------
# 4. reject records reason
# ----------------------------------------------------------------------


def test_learning_reject_records_reason():
    mgr = LearningManager(store=InMemoryLearningStore())
    p = LearningProposal(
        agent_id="agent-3",
        signal_type=LearningSignal.FEEDBACK,
        observation="obs",
        proposed_change="change",
        rationale="why",
    )
    pid = _run(mgr.propose(p))
    _run(mgr.request_review(pid))
    ok = _run(mgr.reject(pid, approver="reviewer-2", reason="Insufficient evidence"))
    assert ok is True
    rec = _run(mgr.get(pid))
    assert rec is not None
    assert rec.status == ProposalStatus.REJECTED
    assert rec.rejection_reason == "Insufficient evidence"
    assert rec.reviewed_by == "reviewer-2"
    assert rec.reviewed_at is not None


# ----------------------------------------------------------------------
# 5. apply() on a REJECTED proposal must raise (status guard)
# ----------------------------------------------------------------------


def test_learning_apply_rejected_proposal_raises():
    mgr = LearningManager(store=InMemoryLearningStore())
    p = LearningProposal(
        agent_id="agent-4",
        signal_type=LearningSignal.FEEDBACK,
        observation="obs",
        proposed_change="change",
        rationale="why",
    )
    pid = _run(mgr.propose(p))
    _run(mgr.request_review(pid))
    _run(mgr.reject(pid, approver="r", reason="no"))
    with pytest.raises(LearningError):
        _run(mgr.apply(pid, applied_by="dev"))


# ----------------------------------------------------------------------
# 6. PROPOSED -> APPLIED directly is FORBIDDEN
# ----------------------------------------------------------------------


def test_learning_apply_without_approval_raises():
    mgr = LearningManager(store=InMemoryLearningStore())
    p = LearningProposal(
        agent_id="agent-5",
        signal_type=LearningSignal.FEEDBACK,
        observation="obs",
        proposed_change="change",
        rationale="why",
    )
    pid = _run(mgr.propose(p))
    # No request_review, no approve — PROPOSED directly to APPLIED is forbidden.
    with pytest.raises(LearningError):
        _run(mgr.apply(pid, applied_by="dev"))
    # Status must still be PROPOSED.
    rec = _run(mgr.get(pid))
    assert rec is not None
    assert rec.status == ProposalStatus.PROPOSED


# ----------------------------------------------------------------------
# 7. supersede
# ----------------------------------------------------------------------


def test_learning_supersede_old_marked_superseded():
    mgr = LearningManager(store=InMemoryLearningStore())
    old = LearningProposal(
        agent_id="agent-6",
        signal_type=LearningSignal.FEEDBACK,
        observation="v1 obs",
        proposed_change="v1 change",
        rationale="v1",
    )
    new = LearningProposal(
        agent_id="agent-6",
        signal_type=LearningSignal.FEEDBACK,
        observation="v2 obs",
        proposed_change="v2 change",
        rationale="v2 supersedes v1",
    )
    old_id = _run(mgr.propose(old))
    new_id = _run(mgr.propose(new))
    assert old_id != new_id

    ok = _run(mgr.supersede(old_id, new_id))
    assert ok is True

    old_rec = _run(mgr.get(old_id))
    new_rec = _run(mgr.get(new_id))
    assert old_rec is not None
    assert new_rec is not None
    assert old_rec.status == ProposalStatus.SUPERSEDED
    assert old_rec.metadata.get("superseded_by") == new_id
    # New proposal stays in its current status (PROPOSED).
    assert new_rec.status == ProposalStatus.PROPOSED
    assert new_rec.metadata.get("superseded_by") is None


# ----------------------------------------------------------------------
# 8. ProposalGenerator.from_feedback
# ----------------------------------------------------------------------


def test_proposal_generator_from_feedback():
    fb_mgr = FeedbackManager(store=InMemoryFeedbackStore())
    # Three positive feedback records targeting skill "skill-X"
    ids: list[str] = []
    for i in range(3):
        fid = _run(
            fb_mgr.record(
                agent_id="agent-7",
                feedback_type=FeedbackType.POSITIVE,
                evaluation=f"skill-X worked well case {i}",
                source=f"eval-{i}",
                source_type="automated",
                skill_id="skill-X",
                confidence=0.8,
                evidence_ids=[f"ev_{i}"],
                metadata={"target_component": "skill"},
            )
        )
        ids.append(fid)

    gen = ProposalGenerator()
    proposal = _run(
        gen.from_feedback(
            agent_id="agent-7",
            feedback_ids=ids,
            feedback_manager=fb_mgr,
        )
    )
    assert proposal is not None
    assert proposal.signal_type == LearningSignal.FEEDBACK
    assert proposal.target_component == "skill"
    assert proposal.target_id == "skill-X"
    assert len(proposal.source_feedback_ids) == 3
    assert set(proposal.source_feedback_ids) == set(ids)
    assert proposal.confidence == pytest.approx(0.8)
    assert set(proposal.evidence_ids) == {f"ev_{i}" for i in range(3)}

    # Only 2 records → no proposal.
    short_proposal = _run(
        gen.from_feedback(
            agent_id="agent-7",
            feedback_ids=ids[:2],
            feedback_manager=fb_mgr,
        )
    )
    assert short_proposal is None


# ----------------------------------------------------------------------
# 9. ProposalGenerator.from_decision_outcome
# ----------------------------------------------------------------------


def test_proposal_generator_from_decision_outcome():
    store = InMemoryMemoryStore()
    dm = DecisionMemory(store=store)
    fb_mgr = FeedbackManager(store=InMemoryFeedbackStore())

    # Record a decision; outcome will be linked afterwards with a negative
    # keyword so the generator triggers.
    decision = DecisionRecord(
        agent_id="agent-8",
        objective="pick provider",
        options_considered=[{"label": "openai"}, {"label": "mock"}],
        decision_taken="Use openai provider",
        reasoning="higher quality",
        evidence_ids=["ev_d1"],
        execution_id="exec-d1",
        correlation_id="corr-d1",
        confidence=0.6,
    )
    did = _run(dm.record_decision(decision, domain="default"))

    # Link a negative outcome — contains keyword "failed".
    _run(dm.link_outcome(did, outcome="operation failed: rate limit exceeded"))

    gen = ProposalGenerator()
    proposal = _run(
        gen.from_decision_outcome(
            agent_id="agent-8",
            decision_id=did,
            decision_memory=dm,
            feedback_manager=fb_mgr,
        )
    )
    assert proposal is not None
    assert proposal.signal_type == LearningSignal.OUTCOME
    assert did in proposal.source_decision_ids
    assert proposal.execution_id == "exec-d1"
    assert proposal.correlation_id == "corr-d1"
    assert proposal.target_component in {"skill", "policy", "tool", "prompt", "other"}

    # Clean decision (no outcome, no negative feedback) → no proposal.
    clean = DecisionRecord(
        agent_id="agent-8",
        objective="pick provider",
        decision_taken="Use mock provider",
        reasoning="safer",
    )
    clean_id = _run(dm.record_decision(clean, domain="default"))
    no_proposal = _run(
        gen.from_decision_outcome(
            agent_id="agent-8",
            decision_id=clean_id,
            decision_memory=dm,
            feedback_manager=fb_mgr,
        )
    )
    assert no_proposal is None


# ----------------------------------------------------------------------
# 10. provenance preserved (correlation_id propagated)
# ----------------------------------------------------------------------


def test_learning_proposal_preserves_provenance():
    mgr = LearningManager(store=InMemoryLearningStore())
    # Open a CorrelationScope; the proposal must inherit its ids.
    with CorrelationTracker.scope(
        correlation_id="corr-preserved",
        execution_id="exec-preserved",
        agent_id="agent-prov",
    ):
        p = LearningProposal(
            agent_id="agent-prov",
            signal_type=LearningSignal.EXPERIENCE,
            observation="trace had warnings",
            proposed_change="tune prompt",
            rationale="warnings indicate ambiguous prompt",
            target_component="prompt",
        )
        pid = _run(mgr.propose(p))

    fetched = _run(mgr.get(pid))
    assert fetched is not None
    assert fetched.correlation_id == "corr-preserved"
    assert fetched.execution_id == "exec-preserved"
    assert fetched.status == ProposalStatus.PROPOSED

    # Queryable by status + agent.
    listed = _run(mgr.query(agent_id="agent-prov", status=ProposalStatus.PROPOSED))
    assert any(r.proposal_id == pid for r in listed)


# ----------------------------------------------------------------------
# Bonus: SQLiteLearningStore round-trip + ExecutionTrace-based proposal
# ----------------------------------------------------------------------


def test_sqlite_learning_store_round_trip():
    import os
    import tempfile

    from albra_seed.learning import SQLiteLearningStore

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "learning.sqlite")
        store1 = SQLiteLearningStore(db_path=path)
        mgr1 = LearningManager(store=store1)
        p = LearningProposal(
            agent_id="agent-sql",
            signal_type=LearningSignal.FEEDBACK,
            observation="obs",
            proposed_change="change",
            rationale="why",
            target_component="skill",
            target_id="skill-S",
            confidence=0.42,
        )
        pid = _run(mgr1.propose(p))
        store1.close()

        store2 = SQLiteLearningStore(db_path=path)
        mgr2 = LearningManager(store=store2)
        fetched = _run(mgr2.get(pid))
        assert fetched is not None
        assert fetched.agent_id == "agent-sql"
        assert fetched.target_component == "skill"
        assert fetched.target_id == "skill-S"
        assert fetched.confidence == pytest.approx(0.42)
        store2.close()


def test_proposal_generator_from_execution_trace_errors():
    fb_mgr = FeedbackManager(store=InMemoryFeedbackStore())
    trace = ExecutionTrace(
        execution_id="exec-trace-1",
        correlation_id="corr-trace-1",
        agent_id="agent-9",
        task="demo",
        errors=["tool X returned 500", "tool X returned 503"],
        warnings=["slow response"],
    )
    gen = ProposalGenerator()
    proposal = _run(
        gen.from_execution_trace(
            agent_id="agent-9",
            execution_id="exec-trace-1",
            trace=trace,
            feedback_manager=fb_mgr,
        )
    )
    assert proposal is not None
    assert proposal.signal_type == LearningSignal.EXPERIENCE
    assert proposal.target_component == "tool"
    assert "exec-trace-1" in proposal.source_execution_ids
    assert proposal.execution_id == "exec-trace-1"
    assert proposal.correlation_id == "corr-trace-1"

    # Clean trace → None.
    clean = ExecutionTrace(execution_id="exec-clean", agent_id="agent-9")
    no_proposal = _run(
        gen.from_execution_trace(
            agent_id="agent-9",
            execution_id="exec-clean",
            trace=clean,
            feedback_manager=fb_mgr,
        )
    )
    assert no_proposal is None
