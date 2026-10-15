"""Persistence and fail-closed validation tests for HITL checkpoints."""
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from albra_seed.approvals.checkpoint import CheckpointValidationError, SuspensionCheckpointV1
from albra_seed.approvals.sqlite_store import SQLiteSuspendedExecutionCoordinator
from albra_seed.approvals.suspension import (
    SuspendedExecutionError,
    canonical_arguments_sha256,
)


def _setup(path):
    now = datetime.now(timezone.utc)
    args = {"to": "recipient@example.test", "body": "checkpoint test"}
    store = SQLiteSuspendedExecutionCoordinator(path)
    store.register(
        approval_id="approval-checkpoint", execution_id="execution-checkpoint",
        correlation_id="correlation-checkpoint", tool_call_id="call-checkpoint",
        tool_name="send_message", arguments=args, principal_id="issuer:subject",
        deadline=now + timedelta(minutes=5),
    )
    checkpoint = SuspensionCheckpointV1(
        approval_id="approval-checkpoint", execution_id="execution-checkpoint",
        correlation_id="correlation-checkpoint", tool_call_id="call-checkpoint",
        tool_name="send_message", arguments_sha256=canonical_arguments_sha256(args),
        principal_id="issuer:subject", runtime_compatibility="agent-v1/mock-v1",
        deadline=(now + timedelta(minutes=5)).isoformat(), created_at=now.isoformat(),
        tool_arguments=args,
    )
    return store, checkpoint, now


def test_checkpoint_survives_new_coordinator_and_validates_runtime_and_deadline(tmp_path):
    path = tmp_path / "checkpoint.sqlite3"
    store, checkpoint, now = _setup(path)
    store.save_checkpoint(checkpoint)

    restarted = SQLiteSuspendedExecutionCoordinator(path)
    loaded = restarted.get_checkpoint(
        checkpoint.approval_id, runtime_compatibility="agent-v1/mock-v1", now=now,
    )
    assert loaded == checkpoint
    with pytest.raises(SuspendedExecutionError, match="runtime compatibility"):
        restarted.get_checkpoint(
            checkpoint.approval_id, runtime_compatibility="agent-v2/mock-v1", now=now,
        )
    with pytest.raises(SuspendedExecutionError, match="deadline"):
        restarted.get_checkpoint(
            checkpoint.approval_id, runtime_compatibility="agent-v1/mock-v1",
            now=now + timedelta(minutes=6),
        )


def test_checkpoint_is_bound_to_durable_approval_and_cannot_be_replaced(tmp_path):
    store, checkpoint, now = _setup(tmp_path / "checkpoint.sqlite3")
    store.save_checkpoint(checkpoint)
    store.save_checkpoint(checkpoint)  # identical retry is idempotent
    from dataclasses import replace
    changed = replace(checkpoint, tool_name="delete_resource")
    with pytest.raises(SuspendedExecutionError, match="binding mismatch"):
        store.save_checkpoint(changed)
    with pytest.raises(SuspendedExecutionError, match="different payload"):
        store.save_checkpoint(replace(checkpoint, runtime_compatibility="other-runtime"))


def test_checkpoint_payload_corruption_fails_closed(tmp_path):
    path = tmp_path / "checkpoint.sqlite3"
    store, checkpoint, now = _setup(path)
    store.save_checkpoint(checkpoint)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE hitl_execution_checkpoints SET payload_json = ? WHERE approval_id = ?",
            (checkpoint.to_json().replace("checkpoint test", "tampered payload"), checkpoint.approval_id),
        )
    restarted = SQLiteSuspendedExecutionCoordinator(path)
    with pytest.raises(SuspendedExecutionError, match="integrity check"):
        restarted.get_checkpoint(
            checkpoint.approval_id, runtime_compatibility="agent-v1/mock-v1", now=now,
        )


def test_checkpoint_cannot_be_saved_for_unknown_approval_or_claimed_execution(tmp_path):
    store, checkpoint, now = _setup(tmp_path / "checkpoint.sqlite3")
    with pytest.raises(SuspendedExecutionError, match="does not exist"):
        store.save_checkpoint(SuspensionCheckpointV1(
            **{**checkpoint.__dict__, "approval_id": "missing-approval"}
        ))
    store.decide(
        checkpoint.approval_id, decision="approve", authenticated_principal_id="reviewer",
        authorized=True, now=now,
    )
    claim = store.claim_resume(
        checkpoint.approval_id, execution_id=checkpoint.execution_id,
        correlation_id=checkpoint.correlation_id, tool_call_id=checkpoint.tool_call_id,
        tool_name=checkpoint.tool_name, arguments=dict(checkpoint.tool_arguments),
        principal_id=checkpoint.principal_id, now=now,
    )
    assert claim.state.value == "claimed"
    with pytest.raises(SuspendedExecutionError, match="not resumable"):
        store.save_checkpoint(checkpoint)
