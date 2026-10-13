"""Process-local HITL suspension coordinator primitives.

This module deliberately does not expose an HTTP endpoint or authenticate callers.
The application must pass a principal obtained from its verified identity mechanism
and an authorization result computed by its policy layer. State is process-local:
it is not safe for multi-worker deployments or restart recovery.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SuspendedExecutionError(RuntimeError):
    """Raised when a suspension cannot safely be registered or resumed."""


class SuspendedExecutionState(str, Enum):
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CLAIMED = "claimed"


@dataclass(frozen=True)
class SuspendedExecution:
    """Immutable binding for the exact tool call awaiting a human decision."""

    approval_id: str
    execution_id: str
    correlation_id: str
    tool_call_id: str
    tool_name: str
    arguments_sha256: str
    principal_id: str
    deadline: datetime
    state: SuspendedExecutionState = SuspendedExecutionState.AWAITING_APPROVAL
    decided_by: str | None = None
    decision_reason: str = ""


def canonical_arguments_sha256(arguments: dict[str, Any]) -> str:
    """Hash JSON arguments with deterministic key ordering and strict JSON values."""
    encoded = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SuspendedExecutionCoordinator:
    """Atomic, in-process decision and resume claims for bound tool calls.

    This is a building block, not a durable store or a complete runtime
    integration. The caller must revalidate current policy, firewall state,
    principal permissions, and the original call immediately before dispatch.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._executions: dict[str, SuspendedExecution] = {}

    def register(
        self,
        *,
        approval_id: str,
        execution_id: str,
        correlation_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        principal_id: str,
        deadline: datetime,
    ) -> SuspendedExecution:
        required = (approval_id, execution_id, correlation_id, tool_call_id,
                    tool_name, principal_id)
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise SuspendedExecutionError("missing required execution binding")
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise SuspendedExecutionError("deadline must be timezone-aware")
        record = SuspendedExecution(
            approval_id=approval_id,
            execution_id=execution_id,
            correlation_id=correlation_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_sha256=canonical_arguments_sha256(arguments),
            principal_id=principal_id,
            deadline=deadline,
        )
        with self._lock:
            if approval_id in self._executions:
                raise SuspendedExecutionError("approval_id already registered")
            self._executions[approval_id] = record
        return record

    def get(self, approval_id: str) -> SuspendedExecution | None:
        with self._lock:
            return self._executions.get(approval_id)

    def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        authenticated_principal_id: str | None,
        authorized: bool,
        now: datetime | None = None,
        reason: str = "",
    ) -> SuspendedExecution:
        """Record one terminal decision; identity/authorization must be verified upstream."""
        if decision not in {"approve", "reject"}:
            raise SuspendedExecutionError("decision must be approve or reject")
        if not authorized or not authenticated_principal_id:
            raise SuspendedExecutionError("authenticated approval permission required")
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise SuspendedExecutionError("now must be timezone-aware")
        with self._lock:
            record = self._executions.get(approval_id)
            if record is None:
                raise SuspendedExecutionError("unknown approval_id")
            if record.state is not SuspendedExecutionState.AWAITING_APPROVAL:
                return record
            if current_time >= record.deadline:
                updated = SuspendedExecution(
                    **{**record.__dict__, "state": SuspendedExecutionState.EXPIRED}
                )
            else:
                updated = SuspendedExecution(
                    **{
                        **record.__dict__,
                        "state": (
                            SuspendedExecutionState.APPROVED
                            if decision == "approve"
                            else SuspendedExecutionState.REJECTED
                        ),
                        "decided_by": authenticated_principal_id,
                        "decision_reason": reason if decision == "reject" else "",
                    }
                )
            self._executions[approval_id] = updated
            return updated

    def claim_resume(
        self,
        approval_id: str,
        *,
        execution_id: str,
        correlation_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        principal_id: str,
        now: datetime | None = None,
    ) -> SuspendedExecution:
        """Atomically claim a matching approved call once; dispatch still needs policy revalidation."""
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None or current_time.utcoffset() is None:
            raise SuspendedExecutionError("now must be timezone-aware")
        with self._lock:
            record = self._executions.get(approval_id)
            if record is None:
                raise SuspendedExecutionError("unknown approval_id")
            if record.state is not SuspendedExecutionState.APPROVED:
                raise SuspendedExecutionError("execution is not approved and resumable")
            if current_time >= record.deadline:
                updated = SuspendedExecution(
                    **{**record.__dict__, "state": SuspendedExecutionState.EXPIRED}
                )
                self._executions[approval_id] = updated
                raise SuspendedExecutionError("approval deadline passed")
            matches = (
                record.execution_id == execution_id
                and record.correlation_id == correlation_id
                and record.tool_call_id == tool_call_id
                and record.tool_name == tool_name
                and record.principal_id == principal_id
                and record.arguments_sha256 == canonical_arguments_sha256(arguments)
            )
            if not matches:
                raise SuspendedExecutionError("resumption binding mismatch")
            claimed = SuspendedExecution(
                **{**record.__dict__, "state": SuspendedExecutionState.CLAIMED}
            )
            self._executions[approval_id] = claimed
            return claimed


__all__ = [
    "SuspendedExecution",
    "SuspendedExecutionCoordinator",
    "SuspendedExecutionError",
    "SuspendedExecutionState",
    "canonical_arguments_sha256",
]
