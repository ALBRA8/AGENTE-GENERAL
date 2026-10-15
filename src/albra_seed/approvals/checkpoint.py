"""Versioned, validated checkpoint payloads for safe HITL suspension boundaries.

This module defines the data contract only. It does not restore Python
coroutines, provider sessions, or authorization verdicts. A caller may create
a checkpoint only from trusted server-side execution state at the explicit
approval suspension boundary; never construct one from client resume input.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

CHECKPOINT_SCHEMA_VERSION = 1
_SAFE_PHASES = frozenset({"awaiting_approval"})
_FORBIDDEN_KEY = re.compile(r"(?:password|passwd|secret|credential|api[_-]?key|access[_-]?token|refresh[_-]?token|bearer|private[_-]?key)", re.I)


class CheckpointValidationError(ValueError):
    """Checkpoint is malformed, incompatible, or unsafe to restore."""


def _json_value(value: Any, *, path: str = "payload") -> Any:
    """Return JSON-safe values and reject credential-like field names."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CheckpointValidationError(f"{path} contains a non-string key")
            if _FORBIDDEN_KEY.search(key):
                raise CheckpointValidationError(f"{path} contains a forbidden credential-like field")
            result[key] = _json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item, path=f"{path}[]") for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        import math
        if not math.isfinite(value):
            raise CheckpointValidationError(f"{path} contains a non-finite number")
        return value
    raise CheckpointValidationError(f"{path} contains a non-JSON value")


def _aware_iso(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise CheckpointValidationError(f"invalid {field}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CheckpointValidationError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class SuspensionCheckpointV1:
    """Application-owned state at the HITL wait boundary; schema version 1."""

    approval_id: str
    execution_id: str
    correlation_id: str
    tool_call_id: str
    tool_name: str
    arguments_sha256: str
    principal_id: str
    runtime_compatibility: str
    deadline: str
    created_at: str
    tool_arguments: Mapping[str, Any]
    schema_version: int = CHECKPOINT_SCHEMA_VERSION
    phase: str = "awaiting_approval"

    def __post_init__(self) -> None:
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointValidationError("unsupported checkpoint schema version")
        if self.phase not in _SAFE_PHASES:
            raise CheckpointValidationError("unsupported checkpoint phase")
        for name in (
            "approval_id", "execution_id", "correlation_id", "tool_call_id",
            "tool_name", "principal_id", "runtime_compatibility",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise CheckpointValidationError(f"missing checkpoint binding: {name}")
        if not re.fullmatch(r"[0-9a-f]{64}", self.arguments_sha256):
            raise CheckpointValidationError("invalid arguments digest")
        deadline = _aware_iso(self.deadline, "deadline")
        created_at = _aware_iso(self.created_at, "created_at")
        if deadline <= created_at:
            raise CheckpointValidationError("checkpoint deadline must follow creation")
        clean_args = _json_value(self.tool_arguments, path="tool_arguments")
        if not isinstance(clean_args, dict):
            raise CheckpointValidationError("tool_arguments must be an object")
        # Replace nested structures with validated JSON-compatible values.
        object.__setattr__(self, "tool_arguments", clean_args)

    def to_json(self) -> str:
        """Serialize deterministically for trusted server-side durable storage."""
        payload = {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "approval_id": self.approval_id,
            "execution_id": self.execution_id,
            "correlation_id": self.correlation_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments_sha256": self.arguments_sha256,
            "principal_id": self.principal_id,
            "runtime_compatibility": self.runtime_compatibility,
            "deadline": _aware_iso(self.deadline, "deadline").isoformat(),
            "created_at": _aware_iso(self.created_at, "created_at").isoformat(),
            "tool_arguments": self.tool_arguments,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def payload_sha256(self) -> str:
        """Checksum for accidental corruption detection, not an authenticity MAC."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_json(cls, raw: str) -> "SuspensionCheckpointV1":
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CheckpointValidationError("invalid checkpoint JSON") from exc
        if not isinstance(payload, dict):
            raise CheckpointValidationError("checkpoint must be a JSON object")
        expected = {
            "schema_version", "phase", "approval_id", "execution_id",
            "correlation_id", "tool_call_id", "tool_name", "arguments_sha256",
            "principal_id", "runtime_compatibility", "deadline", "created_at",
            "tool_arguments",
        }
        if set(payload) != expected:
            raise CheckpointValidationError("checkpoint fields do not match schema")
        return cls(**payload)

    def validate_binding(
        self, *, approval_id: str, execution_id: str, correlation_id: str,
        tool_call_id: str, tool_name: str, arguments_sha256: str,
        principal_id: str, runtime_compatibility: str, now: datetime | None = None,
    ) -> None:
        """Fail closed unless all trusted runtime bindings still match."""
        expected = {
            "approval_id": approval_id,
            "execution_id": execution_id,
            "correlation_id": correlation_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments_sha256": arguments_sha256,
            "principal_id": principal_id,
            "runtime_compatibility": runtime_compatibility,
        }
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise CheckpointValidationError(f"checkpoint binding mismatch: {field}")
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise CheckpointValidationError("now must be timezone-aware")
        if instant.astimezone(timezone.utc) >= _aware_iso(self.deadline, "deadline"):
            raise CheckpointValidationError("checkpoint deadline has expired")
