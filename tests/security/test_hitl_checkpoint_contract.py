"""Fail-closed tests for the versioned HITL suspension checkpoint contract."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from albra_seed.approvals.checkpoint import (
    CheckpointValidationError,
    SuspensionCheckpointV1,
)


def _checkpoint(**overrides):
    now = datetime.now(timezone.utc)
    values = dict(
        approval_id="approval-1",
        execution_id="execution-1",
        correlation_id="correlation-1",
        tool_call_id="call-1",
        tool_name="send_message",
        arguments_sha256="a" * 64,
        principal_id="issuer:subject-1",
        runtime_compatibility="agent-v1/provider-mock-v1",
        deadline=(now + timedelta(minutes=5)).isoformat(),
        created_at=now.isoformat(),
        tool_arguments={"to": "recipient@example.test", "body": "test"},
    )
    values.update(overrides)
    return SuspensionCheckpointV1(**values)


def test_checkpoint_round_trip_is_versioned_and_deterministic():
    checkpoint = _checkpoint()
    restored = SuspensionCheckpointV1.from_json(checkpoint.to_json())
    assert restored == checkpoint
    assert restored.schema_version == 1
    assert restored.payload_sha256 == checkpoint.payload_sha256


def test_checkpoint_rejects_unknown_version_and_unrecognized_fields():
    payload = json.loads(_checkpoint().to_json())
    payload["schema_version"] = 99
    with pytest.raises(CheckpointValidationError, match="unsupported checkpoint schema"):
        SuspensionCheckpointV1.from_json(json.dumps(payload))

    payload = json.loads(_checkpoint().to_json())
    payload["client_resume_payload"] = {"arguments": {"safe": True}}
    with pytest.raises(CheckpointValidationError, match="fields do not match"):
        SuspensionCheckpointV1.from_json(json.dumps(payload))


def test_checkpoint_rejects_credential_like_fields_and_non_json_values():
    with pytest.raises(CheckpointValidationError, match="credential-like"):
        _checkpoint(tool_arguments={"api_key": "must-not-persist"})
    with pytest.raises(CheckpointValidationError, match="non-JSON"):
        _checkpoint(tool_arguments={"value": object()})


def test_checkpoint_requires_all_trusted_bindings_to_match():
    checkpoint = _checkpoint()
    kwargs = dict(
        approval_id="approval-1", execution_id="execution-1",
        correlation_id="correlation-1", tool_call_id="call-1",
        tool_name="send_message", arguments_sha256="a" * 64,
        principal_id="issuer:subject-1",
        runtime_compatibility="agent-v1/provider-mock-v1",
        now=datetime.now(timezone.utc),
    )
    checkpoint.validate_binding(**kwargs)
    for field, value in (
        ("execution_id", "other-execution"),
        ("correlation_id", "other-correlation"),
        ("tool_call_id", "other-call"),
        ("tool_name", "delete_resource"),
        ("arguments_sha256", "b" * 64),
        ("principal_id", "other-principal"),
        ("runtime_compatibility", "incompatible-runtime"),
    ):
        changed = dict(kwargs)
        changed[field] = value
        with pytest.raises(CheckpointValidationError, match="binding mismatch"):
            checkpoint.validate_binding(**changed)


def test_checkpoint_rejects_expired_or_naive_timestamps():
    now = datetime.now(timezone.utc)
    with pytest.raises(CheckpointValidationError, match="deadline must follow creation"):
        _checkpoint(deadline=(now - timedelta(seconds=1)).isoformat(), created_at=now.isoformat())
    with pytest.raises(CheckpointValidationError, match="timezone-aware"):
        _checkpoint(created_at="2026-01-01T00:00:00")
    checkpoint = _checkpoint(deadline=(now + timedelta(seconds=1)).isoformat(), created_at=(now - timedelta(minutes=1)).isoformat())
    with pytest.raises(CheckpointValidationError, match="expired"):
        checkpoint.validate_binding(
            approval_id="approval-1", execution_id="execution-1",
            correlation_id="correlation-1", tool_call_id="call-1",
            tool_name="send_message", arguments_sha256="a" * 64,
            principal_id="issuer:subject-1",
            runtime_compatibility="agent-v1/provider-mock-v1",
            now=now + timedelta(seconds=2),
        )
