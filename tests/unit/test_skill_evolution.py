"""Unit tests for the Skill Evolution subpackage (GAP-4).

Covers:
  * SkillMetrics defaults + success_rate property
  * SkillEvolutionManager.record_execution increments metrics
  * SkillEvolutionManager.record_feedback updates feedback_summary
  * propose_improvement creates a PROPOSED proposal
  * Full pipeline: PROPOSED → REVIEW_REQUIRED → APPROVED → APPLIED
  * reject() records reviewer + reason
  * apply() without prior APPROVED raises SkillEvolutionError (HITL gate)
  * apply() with proposed_version calls SkillRegistry.upgrade_version
  * metrics_snapshot attached to proposal on creation
  * apply() does NOT modify skill code automatically (no register call)
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from albra_seed.core.types import RiskLevel
from albra_seed.skills import (
    SkillContract,
    SkillEvolutionError,
    SkillEvolutionManager,
    SkillExecutor,
    SkillMetrics,
    SkillRegistry,
)


def _run(coro):
    return asyncio.run(coro)


def _make_contract(name: str = "noop_skill", version: str = "1.0.0") -> SkillContract:
    return SkillContract(
        name=name,
        version=version,
        objective="A test skill.",
        inputs={"description": "any input"},
        outputs={"description": "any output"},
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object"},
        permissions=[],
        dependencies=[],
        risk_level=RiskLevel.LOW,
        evidence_required=False,
        tests=["test_noop_skill_basic"],
        acceptance_criteria=["returns success"],
        max_duration_seconds=5,
    )


async def _noop_handler(args, context):
    return {"ok": True, "received": args}


async def _fail_handler(args, context):
    raise RuntimeError("intentional failure")


# ----------------------------------------------------------------------
# 1. SkillMetrics defaults
# ----------------------------------------------------------------------


def test_skill_metrics_defaults():
    m = SkillMetrics(skill_id="skill-A")
    assert m.skill_id == "skill-A"
    assert m.total_executions == 0
    assert m.successful == 0
    assert m.failed == 0
    assert m.avg_duration_ms == 0.0
    assert m.last_executed_at is None
    assert m.last_error is None
    assert m.feedback_summary == {}
    # success_rate property: 0 executions -> 0.0 (no ZeroDivisionError)
    assert m.success_rate == 0.0


def test_skill_metrics_success_rate_property():
    m = SkillMetrics(skill_id="skill-B", total_executions=10, successful=7, failed=3)
    assert m.success_rate == pytest.approx(0.7)


# ----------------------------------------------------------------------
# 2. record_execution increments metrics
# ----------------------------------------------------------------------


def test_skill_evolution_records_execution():
    reg = SkillRegistry()
    contract = _make_contract("skill_metrics_record_exec")
    reg.register(contract, _noop_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)

    # First execution: success, 100ms
    m = _run(mgr.record_execution(contract.skill_id, success=True, duration_ms=100.0))
    assert m.total_executions == 1
    assert m.successful == 1
    assert m.failed == 0
    assert m.avg_duration_ms == pytest.approx(100.0)
    assert m.last_executed_at is not None
    assert m.last_error is None

    # Second execution: failure, 200ms, with an error message
    m = _run(
        mgr.record_execution(
            contract.skill_id,
            success=False,
            duration_ms=200.0,
            error="boom",
        )
    )
    assert m.total_executions == 2
    assert m.successful == 1
    assert m.failed == 1
    # Rolling average: (100 + 200) / 2 == 150
    assert m.avg_duration_ms == pytest.approx(150.0)
    assert m.last_error == "boom"
    assert m.success_rate == pytest.approx(0.5)


# ----------------------------------------------------------------------
# 3. record_feedback updates feedback_summary
# ----------------------------------------------------------------------


def test_skill_evolution_records_feedback():
    reg = SkillRegistry()
    contract = _make_contract("skill_metrics_feedback")
    reg.register(contract, _noop_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)

    _run(mgr.record_feedback(contract.skill_id, feedback_type="positive"))
    _run(mgr.record_feedback(contract.skill_id, feedback_type="positive"))
    _run(mgr.record_feedback(contract.skill_id, feedback_type="negative"))

    m = _run(mgr.get_metrics(contract.skill_id))
    assert m is not None
    assert m.feedback_summary == {"positive": 2, "negative": 1}


# ----------------------------------------------------------------------
# 4. propose_improvement creates a PROPOSED proposal
# ----------------------------------------------------------------------


def test_skill_evolution_propose_improvement():
    reg = SkillRegistry()
    contract = _make_contract("skill_proposal_target", version="1.2.3")
    reg.register(contract, _noop_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)
    pid = _run(
        mgr.propose_improvement(
            skill_id=contract.skill_id,
            change_description="Add retry on transient 5xx",
            rationale="High failure rate observed",
            proposed_version="1.2.4",
            evidence_ids=["ev_1", "ev_2"],
            source_feedback_ids=["fb_1"],
        )
    )
    assert pid.startswith("skillprop_")

    p = _run(mgr.get_proposal(pid))
    assert p is not None
    assert p.skill_id == contract.skill_id
    assert p.current_version == "1.2.3"
    assert p.proposed_version == "1.2.4"
    assert p.change_description == "Add retry on transient 5xx"
    assert p.rationale == "High failure rate observed"
    assert p.status == "proposed"
    assert p.evidence_ids == ["ev_1", "ev_2"]
    assert p.source_feedback_ids == ["fb_1"]
    assert p.reviewed_at is None
    assert p.reviewed_by is None
    assert p.applied_at is None
    assert p.metrics_snapshot is None  # no metrics were recorded before propose
    assert p.created_at is not None


# ----------------------------------------------------------------------
# 5. Full pipeline PROPOSED → REVIEW_REQUIRED → APPROVED → APPLIED
# ----------------------------------------------------------------------


def test_skill_evolution_pipeline_proposed_to_approved_to_applied():
    reg = SkillRegistry()
    contract = _make_contract("skill_pipeline", version="1.0.0")
    reg.register(contract, _noop_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)
    pid = _run(
        mgr.propose_improvement(
            skill_id=contract.skill_id,
            change_description="tweak prompt",
            rationale="reduce warnings",
            proposed_version="1.0.1",
        )
    )

    # PROPOSED -> REVIEW_REQUIRED
    ok = _run(mgr.request_review(pid))
    assert ok is True
    p = _run(mgr.get_proposal(pid))
    assert p is not None and p.status == "review_required"

    # REVIEW_REQUIRED -> APPROVED
    ok = _run(mgr.approve(pid, reviewer="rev-1"))
    assert ok is True
    p = _run(mgr.get_proposal(pid))
    assert p is not None
    assert p.status == "approved"
    assert p.reviewed_by == "rev-1"
    assert p.reviewed_at is not None
    assert p.applied_at is None

    # APPROVED -> APPLIED (calls registry.upgrade_version because proposed_version is set)
    ok = _run(mgr.apply(pid, applied_by="dev-ops"))
    assert ok is True
    p = _run(mgr.get_proposal(pid))
    assert p is not None
    assert p.status == "applied"
    assert p.applied_at is not None
    assert p.metadata.get("applied_by") == "dev-ops"

    # The skill's version was bumped by the existing SkillRegistry.upgrade_version().
    assert reg.get(contract.skill_id).version == "1.0.1"


# ----------------------------------------------------------------------
# 6. reject records reviewer + reason
# ----------------------------------------------------------------------


def test_skill_evolution_reject_records_reason():
    reg = SkillRegistry()
    contract = _make_contract("skill_reject")
    reg.register(contract, _noop_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)
    pid = _run(
        mgr.propose_improvement(
            skill_id=contract.skill_id,
            change_description="x",
            rationale="y",
        )
    )
    _run(mgr.request_review(pid))

    ok = _run(mgr.reject(pid, reviewer="rev-2", reason="Insufficient evidence"))
    assert ok is True
    p = _run(mgr.get_proposal(pid))
    assert p is not None
    assert p.status == "rejected"
    assert p.rejection_reason == "Insufficient evidence"
    assert p.reviewed_by == "rev-2"
    assert p.reviewed_at is not None


# ----------------------------------------------------------------------
# 7. apply() without prior APPROVED raises (HITL gate)
# ----------------------------------------------------------------------


def test_skill_evolution_apply_without_approval_raises():
    reg = SkillRegistry()
    contract = _make_contract("skill_hitl")
    reg.register(contract, _noop_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)
    pid = _run(
        mgr.propose_improvement(
            skill_id=contract.skill_id,
            change_description="change",
            rationale="why",
        )
    )
    # No approve() — PROPOSED -> APPLIED directly is FORBIDDEN.
    with pytest.raises(SkillEvolutionError):
        _run(mgr.apply(pid, applied_by="dev"))

    # Status must still be PROPOSED.
    p = _run(mgr.get_proposal(pid))
    assert p is not None
    assert p.status == "proposed"


def test_skill_evolution_apply_rejected_proposal_raises():
    reg = SkillRegistry()
    contract = _make_contract("skill_hitl_rejected")
    reg.register(contract, _noop_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)
    pid = _run(
        mgr.propose_improvement(
            skill_id=contract.skill_id,
            change_description="change",
            rationale="why",
        )
    )
    _run(mgr.request_review(pid))
    _run(mgr.reject(pid, reviewer="r", reason="no"))

    with pytest.raises(SkillEvolutionError):
        _run(mgr.apply(pid, applied_by="dev"))


# ----------------------------------------------------------------------
# 8. apply() with proposed_version upgrades the version via registry
# ----------------------------------------------------------------------


def test_skill_evolution_apply_upgrades_version():
    reg = SkillRegistry()
    contract = _make_contract("skill_version_bump", version="2.1.0")
    reg.register(contract, _noop_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)
    pid = _run(
        mgr.propose_improvement(
            skill_id=contract.skill_id,
            change_description="major change",
            rationale="security patch",
            proposed_version="2.2.0",
        )
    )
    _run(mgr.approve(pid, reviewer="rev-3"))

    # Before apply, version is unchanged
    assert reg.get(contract.skill_id).version == "2.1.0"

    _run(mgr.apply(pid, applied_by="dev-ops"))

    # After apply, SkillRegistry.upgrade_version() was called.
    assert reg.get(contract.skill_id).version == "2.2.0"

    # Lifecycle history reflects the version bump.
    history = reg.lifecycle.history(contract.skill_id)
    assert any("version upgrade" in e.reason for e in history)
    # And specifically the 2.1.0 -> 2.2.0 upgrade is recorded.
    assert any(
        "2.1.0 -> 2.2.0" in e.reason
        for e in history
    )


# ----------------------------------------------------------------------
# 9. metrics_snapshot attached to proposal on creation
# ----------------------------------------------------------------------


def test_skill_evolution_metrics_snapshot_in_proposal():
    reg = SkillRegistry()
    contract = _make_contract("skill_snapshot", version="1.0.0")
    reg.register(contract, _noop_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)
    # Record some metrics first
    _run(mgr.record_execution(contract.skill_id, success=True, duration_ms=50.0))
    _run(mgr.record_execution(contract.skill_id, success=False, duration_ms=150.0, error="x"))
    _run(mgr.record_feedback(contract.skill_id, feedback_type="positive"))
    _run(mgr.record_feedback(contract.skill_id, feedback_type="negative"))

    pid = _run(
        mgr.propose_improvement(
            skill_id=contract.skill_id,
            change_description="fix the failures",
            rationale="2nd execution failed",
        )
    )
    p = _run(mgr.get_proposal(pid))
    assert p is not None
    assert p.metrics_snapshot is not None
    snap = p.metrics_snapshot
    assert snap.skill_id == contract.skill_id
    assert snap.total_executions == 2
    assert snap.successful == 1
    assert snap.failed == 1
    assert snap.avg_duration_ms == pytest.approx(100.0)
    assert snap.last_error == "x"
    assert snap.feedback_summary == {"positive": 1, "negative": 1}
    assert snap.success_rate == pytest.approx(0.5)

    # Recording MORE metrics after the snapshot must NOT mutate the snapshot.
    _run(mgr.record_execution(contract.skill_id, success=True, duration_ms=999.0))
    p2 = _run(mgr.get_proposal(pid))
    assert p2 is not None
    assert p2.metrics_snapshot is not None
    assert p2.metrics_snapshot.total_executions == 2  # still the snapshot value
    # The live metrics now have 3 executions.
    live = _run(mgr.get_metrics(contract.skill_id))
    assert live is not None
    assert live.total_executions == 3


# ----------------------------------------------------------------------
# 10. apply() does NOT call SkillRegistry.register (no code modification)
# ----------------------------------------------------------------------


def test_skill_evolution_does_not_modify_skill_code_automatically():
    """Verify that apply() does NOT re-register the skill. The only
    SkillRegistry mutation allowed is upgrade_version() (when
    proposed_version is set). We use a MagicMock registry to assert
    which methods were called.
    """
    mock_registry = MagicMock()
    # Provide a .get() that returns an object with .version = "1.0.0"
    fake_contract = MagicMock()
    fake_contract.version = "1.0.0"
    mock_registry.get.return_value = fake_contract
    # upgrade_version returns the new version string (matches real registry).
    mock_registry.upgrade_version.return_value = "1.0.1"

    mgr = SkillEvolutionManager(skill_registry=mock_registry)
    pid = _run(
        mgr.propose_improvement(
            skill_id="skill-mock",
            change_description="change",
            rationale="why",
            proposed_version="1.0.1",
        )
    )
    _run(mgr.approve(pid, reviewer="rev"))
    _run(mgr.apply(pid, applied_by="dev"))

    # The ONLY mutation API called on the registry is upgrade_version.
    mock_registry.upgrade_version.assert_called_once_with("skill-mock", "1.0.1")
    # Crucially, register() was NEVER called by the evolution manager.
    mock_registry.register.assert_not_called()
    # And no other mutating methods were called (disable/enable/remove).
    mock_registry.disable.assert_not_called()
    mock_registry.enable.assert_not_called()
    mock_registry.remove.assert_not_called()


def test_skill_evolution_apply_without_proposed_version_skips_upgrade():
    """When proposed_version is None, apply() does NOT call
    upgrade_version at all — the proposal is a code-only tweak."""
    mock_registry = MagicMock()
    fake_contract = MagicMock()
    fake_contract.version = "1.0.0"
    mock_registry.get.return_value = fake_contract

    mgr = SkillEvolutionManager(skill_registry=mock_registry)
    pid = _run(
        mgr.propose_improvement(
            skill_id="skill-mock-2",
            change_description="tweak",
            rationale="refactor",
            # proposed_version=None
        )
    )
    _run(mgr.approve(pid, reviewer="rev"))
    ok = _run(mgr.apply(pid, applied_by="dev"))
    assert ok is True
    mock_registry.upgrade_version.assert_not_called()
    mock_registry.register.assert_not_called()


# ----------------------------------------------------------------------
# Bonus: list_proposals filters by skill_id and status
# ----------------------------------------------------------------------


def test_skill_evolution_list_proposals_filters():
    reg = SkillRegistry()
    c1 = _make_contract("skill_list_1", version="1.0.0")
    c2 = _make_contract("skill_list_2", version="1.0.0")
    reg.register(c1, _noop_handler)
    reg.register(c2, _noop_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)
    p1 = _run(mgr.propose_improvement(skill_id=c1.skill_id, change_description="a", rationale="b"))
    p2 = _run(mgr.propose_improvement(skill_id=c1.skill_id, change_description="c", rationale="d"))
    p3 = _run(mgr.propose_improvement(skill_id=c2.skill_id, change_description="e", rationale="f"))

    _run(mgr.request_review(p2))
    _run(mgr.approve(p2, reviewer="r"))

    # Filter by skill_id
    listed_c1 = _run(mgr.list_proposals(skill_id=c1.skill_id))
    assert {p.proposal_id for p in listed_c1} == {p1, p2}

    # Filter by status
    listed_approved = _run(mgr.list_proposals(status="approved"))
    assert {p.proposal_id for p in listed_approved} == {p2}

    listed_proposed = _run(mgr.list_proposals(status="proposed"))
    assert {p.proposal_id for p in listed_proposed} == {p1, p3}

    # No filters: all three
    all_listed = _run(mgr.list_proposals())
    assert len(all_listed) == 3


# ----------------------------------------------------------------------
# Bonus: SkillExecutor hook — record_execution called after execute
# ----------------------------------------------------------------------


def test_skill_executor_records_evolution_metrics_after_execute():
    """When a SkillExecutor is constructed with evolution_manager=...,
    each successful execution calls record_execution on the manager.
    A failing handler also records the failure."""
    reg = SkillRegistry()
    contract_ok = _make_contract("skill_executor_ok", version="1.0.0")
    contract_fail = _make_contract("skill_executor_fail", version="1.0.0")
    reg.register(contract_ok, _noop_handler)
    reg.register(contract_fail, _fail_handler)

    mgr = SkillEvolutionManager(skill_registry=reg)
    # Build a NEW executor wired to the same lifecycle+validator+evolution manager.
    new_executor = SkillExecutor(
        validator=reg.validator,
        lifecycle=reg.lifecycle,
        evolution_manager=mgr,
    )
    # Re-register the skills on a registry that uses our wired executor.
    reg2 = SkillRegistry(executor=new_executor)
    reg2.register(contract_ok, _noop_handler)
    reg2.register(contract_fail, _fail_handler)

    # Execute the OK skill — should record one success.
    result_ok = _run(reg2.execute("skill_executor_ok", {"x": 1}))
    assert result_ok.success
    m_ok = _run(mgr.get_metrics(contract_ok.skill_id))
    assert m_ok is not None
    assert m_ok.total_executions == 1
    assert m_ok.successful == 1
    assert m_ok.failed == 0

    # Execute the failing skill — should record one failure with error message.
    result_fail = _run(reg2.execute("skill_executor_fail", {}))
    assert not result_fail.success
    m_fail = _run(mgr.get_metrics(contract_fail.skill_id))
    assert m_fail is not None
    assert m_fail.total_executions == 1
    assert m_fail.successful == 0
    assert m_fail.failed == 1
    assert m_fail.last_error is not None
    assert "intentional failure" in m_fail.last_error


def test_skill_executor_without_evolution_manager_still_works():
    """A SkillExecutor constructed WITHOUT evolution_manager must still
    execute normally (no AttributeError, no metric recording). This
    is the regression guard for existing tests."""
    reg = SkillRegistry()
    contract = _make_contract("skill_no_evo")
    reg.register(contract, _noop_handler)
    result = _run(reg.execute("skill_no_evo", {"x": 1}))
    assert result.success
    assert result.output["ok"] is True
