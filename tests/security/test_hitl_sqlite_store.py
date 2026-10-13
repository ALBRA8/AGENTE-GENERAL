"""Cross-instance and restart regressions for durable HITL suspension."""
import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from datetime import datetime, timedelta, timezone

import pytest

from albra_seed.approvals.sqlite_store import SQLiteSuspendedExecutionCoordinator
from albra_seed.approvals.suspension import (
    SuspendedExecutionError,
    SuspendedExecutionState,
)


def _registered(path):
    now = datetime.now(timezone.utc)
    coordinator = SQLiteSuspendedExecutionCoordinator(path)
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


def _claim(coordinator, now):
    return coordinator.claim_resume(
        "approval-1",
        execution_id="execution-1",
        correlation_id="correlation-1",
        tool_call_id="call-1",
        tool_name="send_message",
        arguments={"to": "user@example.test", "body": "hello"},
        principal_id="principal-1",
        now=now,
    )


def test_pending_and_approved_state_survive_coordinator_restart(tmp_path):
    path = tmp_path / "approvals.sqlite3"
    first, now = _registered(path)
    assert first.get("approval-1").state is SuspendedExecutionState.AWAITING_APPROVAL

    restarted = SQLiteSuspendedExecutionCoordinator(path)
    assert restarted.get("approval-1").execution_id == "execution-1"
    _approve(restarted, now)

    another_restart = SQLiteSuspendedExecutionCoordinator(path)
    assert another_restart.get("approval-1").state is SuspendedExecutionState.APPROVED
    assert _claim(another_restart, now).state is SuspendedExecutionState.CLAIMED


def test_independent_workers_can_claim_only_once(tmp_path):
    path = tmp_path / "approvals.sqlite3"
    coordinator, now = _registered(path)
    _approve(coordinator, now)
    workers = [SQLiteSuspendedExecutionCoordinator(path) for _ in range(12)]

    def attempt(worker):
        try:
            _claim(worker, now)
            return True
        except SuspendedExecutionError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, workers))
    assert sum(results) == 1
    assert coordinator.get("approval-1").state is SuspendedExecutionState.CLAIMED


def test_competing_decisions_have_one_persisted_terminal_state(tmp_path):
    path = tmp_path / "approvals.sqlite3"
    coordinator, now = _registered(path)
    workers = [SQLiteSuspendedExecutionCoordinator(path) for _ in range(2)]

    def decide(args):
        worker, decision = args
        return worker.decide(
            "approval-1",
            decision=decision,
            authenticated_principal_id="reviewer-" + decision,
            authorized=True,
            now=now,
        ).state

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(decide, [(workers[0], "approve"), (workers[1], "reject")]))
    assert states[0] == states[1]
    assert states[0] in {
        SuspendedExecutionState.APPROVED,
        SuspendedExecutionState.REJECTED,
    }
    assert coordinator.get("approval-1").state == states[0]


def test_claim_is_not_reclaimed_after_restart_if_worker_crashes_before_dispatch(tmp_path):
    """Fail closed at the uncertain boundary rather than retrying a side effect."""
    path = tmp_path / "approvals.sqlite3"
    coordinator, now = _registered(path)
    _approve(coordinator, now)
    assert _claim(coordinator, now).state is SuspendedExecutionState.CLAIMED

    # Simulate a worker dying immediately after the durable claim. A new worker
    # must not re-run dispatch merely because it cannot know whether it happened.
    recovered = SQLiteSuspendedExecutionCoordinator(path)
    with pytest.raises(SuspendedExecutionError, match="not approved"):
        _claim(recovered, now)
    assert recovered.get("approval-1").state is SuspendedExecutionState.CLAIMED


def test_deadline_expiry_is_durable_and_prevents_resume(tmp_path):
    path = tmp_path / "approvals.sqlite3"
    coordinator, now = _registered(path)
    _approve(coordinator, now)
    with pytest.raises(SuspendedExecutionError, match="deadline"):
        _claim(coordinator, now + timedelta(minutes=6))
    restarted = SQLiteSuspendedExecutionCoordinator(path)
    assert restarted.get("approval-1").state is SuspendedExecutionState.EXPIRED


def test_changed_binding_and_unauthorized_decision_fail_closed(tmp_path):
    path = tmp_path / "approvals.sqlite3"
    coordinator, now = _registered(path)
    with pytest.raises(SuspendedExecutionError, match="authenticated"):
        coordinator.decide(
            "approval-1",
            decision="approve",
            authenticated_principal_id=None,
            authorized=False,
            now=now,
        )
    assert coordinator.get("approval-1").state is SuspendedExecutionState.AWAITING_APPROVAL
    _approve(coordinator, now)
    with pytest.raises(SuspendedExecutionError, match="binding mismatch"):
        coordinator.claim_resume(
            "approval-1",
            execution_id="other-execution",
            correlation_id="correlation-1",
            tool_call_id="call-1",
            tool_name="send_message",
            arguments={"to": "user@example.test", "body": "hello"},
            principal_id="principal-1",
            now=now,
        )
    assert coordinator.get("approval-1").state is SuspendedExecutionState.APPROVED


def test_corrupt_database_fails_closed(tmp_path):
    path = tmp_path / "corrupt.sqlite3"
    path.write_bytes(b"not a sqlite database")
    with pytest.raises(SuspendedExecutionError, match="initialization failed"):
        SQLiteSuspendedExecutionCoordinator(path)


def test_duplicate_approval_id_is_rejected_durably(tmp_path):
    path = tmp_path / "approvals.sqlite3"
    coordinator, now = _registered(path)
    with pytest.raises(SuspendedExecutionError, match="already registered"):
        coordinator.register(
            approval_id="approval-1",
            execution_id="other-execution",
            correlation_id="correlation-2",
            tool_call_id="call-2",
            tool_name="send_message",
            arguments={"to": "other@example.test"},
            principal_id="principal-1",
            deadline=now + timedelta(minutes=5),
        )



def test_dispatch_outcome_is_durable_and_not_overwritable(tmp_path):
    path = tmp_path / "approvals.sqlite3"
    coordinator, now = _registered(path)
    _approve(coordinator, now)
    _claim(coordinator, now)
    outcome = coordinator.record_dispatch_outcome(
        "approval-1", outcome="unknown", detail="worker died near external dispatch", observed_at=now
    )
    restarted = SQLiteSuspendedExecutionCoordinator(path)
    assert restarted.get_dispatch_outcome("approval-1") == outcome
    # An identical retry is idempotent; a contradictory outcome cannot overwrite audit state.
    assert restarted.record_dispatch_outcome(
        "approval-1", outcome="unknown", detail="worker died near external dispatch", observed_at=now
    ) == outcome
    with pytest.raises(SuspendedExecutionError, match="already recorded"):
        restarted.record_dispatch_outcome(
            "approval-1", outcome="succeeded", detail="conflicting report", observed_at=now
        )


def test_waiter_observes_decision_from_an_independent_worker(tmp_path):
    path = tmp_path / "approvals.sqlite3"
    coordinator, now = _registered(path)
    independent_worker = SQLiteSuspendedExecutionCoordinator(path)

    async def scenario():
        waiter = asyncio.create_task(
            coordinator.wait_for_decision("approval-1", timeout=2.0, poll_interval=0.01)
        )
        await asyncio.sleep(0.03)
        _approve(independent_worker, now)
        result = await waiter
        assert result.state is SuspendedExecutionState.APPROVED

    asyncio.run(scenario())



def _process_claim_once(database_path, now_iso):
    coordinator = SQLiteSuspendedExecutionCoordinator(database_path)
    now = datetime.fromisoformat(now_iso)
    try:
        coordinator.claim_resume(
            "approval-1",
            execution_id="execution-1",
            correlation_id="correlation-1",
            tool_call_id="call-1",
            tool_name="send_message",
            arguments={"to": "user@example.test", "body": "hello"},
            principal_id="principal-1",
            now=now,
        )
        return True
    except SuspendedExecutionError:
        return False


def test_independent_processes_cannot_claim_the_same_dispatch(tmp_path):
    path = tmp_path / "approvals.sqlite3"
    coordinator, now = _registered(path)
    _approve(coordinator, now)
    with ProcessPoolExecutor(max_workers=6, mp_context=get_context("spawn")) as pool:
        results = list(pool.map(
            _process_claim_once,
            [str(path)] * 6,
            [now.isoformat()] * 6,
        ))
    assert sum(results) == 1
    assert coordinator.get("approval-1").state is SuspendedExecutionState.CLAIMED


def test_crash_after_external_effect_before_outcome_is_not_replayed(tmp_path):
    """An unknown side-effect outcome remains claimed after a worker restart."""
    path = tmp_path / "approvals.sqlite3"
    coordinator, now = _registered(path)
    _approve(coordinator, now)
    _claim(coordinator, now)

    # Model an external service accepting the effect, followed by a process
    # crash before the caller could persist a success outcome.
    simulated_external_effects = ["message accepted"]
    recovered = SQLiteSuspendedExecutionCoordinator(path)
    with pytest.raises(SuspendedExecutionError, match="not approved"):
        _claim(recovered, now)

    # Recovery does not retry the effect to guess whether the first attempt ran.
    assert simulated_external_effects == ["message accepted"]
    assert recovered.get("approval-1").state is SuspendedExecutionState.CLAIMED
    assert recovered.get_dispatch_outcome("approval-1") is None
