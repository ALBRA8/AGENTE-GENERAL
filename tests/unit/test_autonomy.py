"""Unit tests for the autonomy subpackage (F2-D).

Covers the AutonomyMatrix mapping, the AutonomyManager.can_perform
check, and the L5-can-perform-L0 invariant.
"""

from __future__ import annotations

import pytest

from albra_seed.autonomy import (
    ActionType,
    AutonomyLevel,
    AutonomyManager,
    AutonomyMatrix,
    AutonomyProfile,
)
from albra_seed.core.types import RiskLevel


# =====================================================================
# AUTONOMY MATRIX
# =====================================================================


def test_autonomy_matrix_maps_action_to_level():
    """The default AutonomyMatrix returns the expected levels."""
    m = AutonomyMatrix()

    # OBSERVE action — only L0 needed, regardless of risk.
    assert m.required_level(ActionType.OBSERVE, RiskLevel.LOW) == AutonomyLevel.L0_OBSERVE
    assert m.required_level(ActionType.OBSERVE, RiskLevel.CRITICAL) == AutonomyLevel.L0_OBSERVE

    # RECOMMEND — L1.
    assert m.required_level(ActionType.RECOMMEND, RiskLevel.LOW) == AutonomyLevel.L1_RECOMMEND

    # EXECUTE_SAFE — L2 for low/medium, L3 for high/critical.
    assert m.required_level(ActionType.EXECUTE_SAFE, RiskLevel.LOW) == AutonomyLevel.L2_EXECUTE_SAFE
    assert m.required_level(ActionType.EXECUTE_SAFE, RiskLevel.HIGH) == AutonomyLevel.L3_EXECUTE_EXTERNAL

    # EXECUTE_EXTERNAL — L3 for low, L4 for high.
    assert m.required_level(ActionType.EXECUTE_EXTERNAL, RiskLevel.LOW) == AutonomyLevel.L3_EXECUTE_EXTERNAL
    assert m.required_level(ActionType.EXECUTE_EXTERNAL, RiskLevel.HIGH) == AutonomyLevel.L4_EXECUTE_SENSITIVE

    # EXECUTE_SENSITIVE — always L4.
    assert m.required_level(ActionType.EXECUTE_SENSITIVE, RiskLevel.LOW) == AutonomyLevel.L4_EXECUTE_SENSITIVE

    # SELF_IMPROVE — always L5.
    assert m.required_level(ActionType.SELF_IMPROVE, RiskLevel.LOW) == AutonomyLevel.L5_SELF_IMPROVE

    # Unknown action type → DEFAULT-DENY → L5.
    assert m.required_level("unknown.action", RiskLevel.LOW) == AutonomyLevel.L5_SELF_IMPROVE

    # Overrides respected.
    m2 = AutonomyMatrix(overrides={
        ("custom.action", RiskLevel.LOW): AutonomyLevel.L2_EXECUTE_SAFE,
    })
    assert m2.required_level("custom.action", RiskLevel.LOW) == AutonomyLevel.L2_EXECUTE_SAFE

    # as_dict exposes all known entries.
    d = m.as_dict()
    assert (ActionType.SELF_IMPROVE, RiskLevel.CRITICAL) in d
    assert ActionType.SELF_IMPROVE in m.known_action_types()


# =====================================================================
# AUTONOMY MANAGER
# =====================================================================


def test_autonomy_manager_checks_sufficient():
    """AutonomyManager.can_perform returns True iff agent_level >= required_level."""
    mgr = AutonomyManager()

    # L2 agent performing EXECUTE_SAFE / LOW → required L2 → OK.
    assert mgr.can_perform_action(
        AutonomyLevel.L2_EXECUTE_SAFE,
        ActionType.EXECUTE_SAFE,
        RiskLevel.LOW,
    ) is True

    # L0 agent cannot perform EXECUTE_SAFE (requires L2).
    assert mgr.can_perform_action(
        AutonomyLevel.L0_OBSERVE,
        ActionType.EXECUTE_SAFE,
        RiskLevel.LOW,
    ) is False

    # L3 agent can perform EXECUTE_EXTERNAL / LOW (requires L3).
    assert mgr.can_perform_action(
        AutonomyLevel.L3_EXECUTE_EXTERNAL,
        ActionType.EXECUTE_EXTERNAL,
        RiskLevel.LOW,
    ) is True

    # L3 agent cannot perform EXECUTE_EXTERNAL / HIGH (requires L4).
    assert mgr.can_perform_action(
        AutonomyLevel.L3_EXECUTE_EXTERNAL,
        ActionType.EXECUTE_EXTERNAL,
        RiskLevel.HIGH,
    ) is False

    # SELF_IMPROVE requires L5 — L4 cannot.
    assert mgr.can_perform_action(
        AutonomyLevel.L4_EXECUTE_SENSITIVE,
        ActionType.SELF_IMPROVE,
        RiskLevel.LOW,
    ) is False

    # Direct can_perform comparison.
    assert mgr.can_perform(AutonomyLevel.L3_EXECUTE_EXTERNAL, AutonomyLevel.L2_EXECUTE_SAFE) is True
    assert mgr.can_perform(AutonomyLevel.L1_RECOMMEND, AutonomyLevel.L3_EXECUTE_EXTERNAL) is False


def test_autonomy_l5_can_perform_l0_actions():
    """An L5 agent can perform any L0 action — autonomy is monotonic."""
    mgr = AutonomyManager()

    # L5 agent can do everything in the matrix.
    for action_type in [
        ActionType.OBSERVE,
        ActionType.RECOMMEND,
        ActionType.EXECUTE_SAFE,
        ActionType.EXECUTE_EXTERNAL,
        ActionType.EXECUTE_SENSITIVE,
        ActionType.SELF_IMPROVE,
    ]:
        for risk in [
            RiskLevel.LOW,
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
            RiskLevel.UNKNOWN,
        ]:
            assert mgr.can_perform_action(
                AutonomyLevel.L5_SELF_IMPROVE, action_type, risk
            ) is True, f"L5 should be able to perform {action_type}/{risk}"

    # L5 >= L0 by direct comparison.
    assert mgr.can_perform(AutonomyLevel.L5_SELF_IMPROVE, AutonomyLevel.L0_OBSERVE) is True


# =====================================================================
# AUTONOMY PROFILE
# =====================================================================


def test_autonomy_profile_allows_and_forbids_action_types():
    """AutonomyProfile.allows_action_type honors allowed/forbidden lists."""
    prof = AutonomyProfile(
        agent_id="a1",
        autonomy_level=AutonomyLevel.L3_EXECUTE_EXTERNAL,
        max_autonomous_risk=RiskLevel.MEDIUM,
        allowed_action_types=[
            ActionType.OBSERVE,
            ActionType.RECOMMEND,
            ActionType.EXECUTE_SAFE,
        ],
        forbidden_action_types=[ActionType.SELF_IMPROVE],
    )

    # Allowed action types pass.
    assert prof.allows_action_type(ActionType.OBSERVE) is True
    assert prof.allows_action_type(ActionType.RECOMMEND) is True

    # Not-in-allowed-list → denied.
    assert prof.allows_action_type(ActionType.EXECUTE_EXTERNAL) is False

    # Explicitly forbidden → denied.
    assert prof.allows_action_type(ActionType.SELF_IMPROVE) is False

    # Risk threshold respected.
    assert prof.can_run_risk(RiskLevel.LOW) is True
    assert prof.can_run_risk(RiskLevel.MEDIUM) is True
    assert prof.can_run_risk(RiskLevel.HIGH) is False
    assert prof.can_run_risk(RiskLevel.CRITICAL) is False

    # Empty allowed list = everything (except forbidden).
    open_prof = AutonomyProfile(
        agent_id="a2",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        forbidden_action_types=[ActionType.SELF_IMPROVE],
    )
    assert open_prof.allows_action_type(ActionType.OBSERVE) is True
    assert open_prof.allows_action_type(ActionType.SELF_IMPROVE) is False

    # touch() updates the timestamp.
    old = open_prof.updated_at
    open_prof.touch()
    assert open_prof.updated_at is not None
    assert open_prof.updated_at != old
