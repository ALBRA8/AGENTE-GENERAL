"""Security regressions for the process-local HITL suspension coordinator."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from albra_seed.approvals.suspension import (
    SuspendedExecutionCoordinator,
    SuspendedExecutionError,
    SuspendedExecutionState,
)


def _registered():
    coordinator = SuspendedExecutionCoordinator()
    now = datetime.now(timezone.utc)
    coordinator.register(
        approval_id="approval-1",
        execution_id="execution-1",
        correlation_id="correlation-1",
        tool_call_id="call-1",
        tool_name="send_message",
        arguments={"to": "user@example.test", "body": "hello"},
        principal_id="principal-1",
        deadline=now + timedelta(minutes=5),
    )
    return coordinator, now


def _approve(coordinator, now):
    return coordinator.decide(
        "approval-1",
        decision="approve",
        authenticated_principal_id="reviewer-1",
        authorized=True,
        now=now,
    )


def _claim(coordinator, now, **overrides):
    values = {
        "approval_id": "approval-1",
        "execution_id": "execution-1",
        "correlation_id": "correlation-1",
        "tool_call_id": "call-1",
        "tool_name": "send_message",
        "arguments": {"to": "user@example.test", "body": "hello"},
        "principal_id": "principal-1",
        "now": now,
    }
    values.update(overrides)
    return coordinator.claim_resume(**values)



def test_suspended_state_is_process_local_and_not_recovered_by_new_coordinator():
    """A fresh coordinator has no implicit recovery of volatile pending state."""
    coordinator, _ = _registered()
    restarted = SuspendedExecutionCoordinator()

    assert coordinator.get("approval-1") is not None
    assert restarted.get("approval-1") is None

def test_pending_execution_is_not_resumable():
    coordinator, now = _registered()
    with pytest.raises(SuspendedExecutionError, match="not approved"):
        _claim(coordinator, now)


def test_decision_fails_closed_without_verified_authorized_identity():
    coordinator, now = _registered()
    with pytest.raises(SuspendedExecutionError, match="authenticated"):
        coordinator.decide(
            "approval-1", decision="approve",
            authenticated_principal_id=None, authorized=False, now=now,
        )
    assert coordinator.get("approval-1").state is SuspendedExecutionState.AWAITING_APPROVAL


def test_approved_bound_call_can_be_claimed_once():
    coordinator, now = _registered()
    _approve(coordinator, now)
    claimed = _claim(coordinator, now)
    assert claimed.state is SuspendedExecutionState.CLAIMED
    with pytest.raises(SuspendedExecutionError, match="not approved"):
        _claim(coordinator, now)


@pytest.mark.parametrize(
    "override",
    [
        {"execution_id": "other-execution"},
        {"correlation_id": "other-correlation"},
        {"tool_call_id": "other-call"},
        {"tool_name": "delete_account"},
        {"arguments": {"to": "attacker@example.test", "body": "hello"}},
        {"principal_id": "other-principal"},
    ],
)
def test_resume_rejects_changed_binding(override):
    coordinator, now = _registered()
    _approve(coordinator, now)
    with pytest.raises(SuspendedExecutionError, match="binding mismatch"):
        _claim(coordinator, now, **override)
    assert coordinator.get("approval-1").state is SuspendedExecutionState.APPROVED


def test_deadline_expires_without_claiming():
    coordinator, now = _registered()
    _approve(coordinator, now)
    with pytest.raises(SuspendedExecutionError, match="deadline"):
        _claim(coordinator, now + timedelta(minutes=6))
    assert coordinator.get("approval-1").state is SuspendedExecutionState.EXPIRED


def test_concurrent_resume_claims_have_exactly_one_winner():
    coordinator, now = _registered()
    _approve(coordinator, now)

    def attempt(_):
        try:
            _claim(coordinator, now)
            return True
        except SuspendedExecutionError:
            return False

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(attempt, range(64)))
    assert sum(results) == 1
    assert coordinator.get("approval-1").state is SuspendedExecutionState.CLAIMED


def test_conflicting_decisions_have_one_terminal_winner():
    coordinator, now = _registered()

    def attempt(decision):
        return coordinator.decide(
            "approval-1", decision=decision,
            authenticated_principal_id=f"reviewer-{decision}",
            authorized=True, now=now,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["approve", "reject"]))
    states = {result.state for result in results}
    assert len(states) == 1
    assert next(iter(states)) in {
        SuspendedExecutionState.APPROVED,
        SuspendedExecutionState.REJECTED,
    }
