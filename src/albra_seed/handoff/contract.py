"""Handoff contract models — formal request/response between ALBRA agents.

A `HandoffRequest` carries the full execution context needed for another
ALBRA agent to take over: objective, input, constraints, requested output
format, risk level, deadline and a stable `execution_id` that travels
across both agents so the provenance chain stays intact.

A `HandoffResponse` carries the outcome: status, result, evidence,
confidence, and the warnings/errors the receiving agent surfaced.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import RiskLevel, utc_now


class HandoffStatus(str):
    """Handoff lifecycle states. Kept as a str subclass so values are
    JSON-serialisable without a custom encoder."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class HandoffRequest(BaseModel):
    """Formal handoff request from one ALBRA agent to another."""

    execution_id: str
    requester: str
    target: str
    objective: str
    input: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    requested_output: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.UNKNOWN
    deadline: datetime | None = None
    correlation_id: str | None = None
    parent_execution_id: str | None = None
    requested_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def deadline_in_seconds(self) -> float | None:
        if self.deadline is None:
            return None
        delta = (self.deadline - utc_now()).total_seconds()
        return max(0.0, delta)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.deadline is None:
            return False
        return (now or utc_now()) >= self.deadline

    def with_timeout(self, seconds: float) -> "HandoffRequest":
        self.deadline = utc_now() + timedelta(seconds=seconds)
        return self


class HandoffResponse(BaseModel):
    """Response returned by the receiving ALBRA agent."""

    execution_id: str
    status: str = HandoffStatus.PENDING
    result: Any = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    responder: str | None = None
    responded_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def success(
        cls,
        execution_id: str,
        *,
        result: Any,
        confidence: float = 0.0,
        evidence: list[dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
        responder: str | None = None,
    ) -> "HandoffResponse":
        return cls(
            execution_id=execution_id,
            status=HandoffStatus.SUCCEEDED,
            result=result,
            confidence=confidence,
            evidence=evidence or [],
            warnings=warnings or [],
            responder=responder,
        )

    @classmethod
    def failure(
        cls,
        execution_id: str,
        *,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        responder: str | None = None,
    ) -> "HandoffResponse":
        return cls(
            execution_id=execution_id,
            status=HandoffStatus.FAILED,
            errors=errors or [],
            warnings=warnings or [],
            responder=responder,
        )

    @classmethod
    def timeout(
        cls,
        execution_id: str,
        *,
        responder: str | None = None,
        message: str = "handoff deadline exceeded",
    ) -> "HandoffResponse":
        return cls(
            execution_id=execution_id,
            status=HandoffStatus.TIMEOUT,
            errors=[message],
            responder=responder,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            HandoffStatus.SUCCEEDED,
            HandoffStatus.FAILED,
            HandoffStatus.TIMEOUT,
            HandoffStatus.REJECTED,
            HandoffStatus.CANCELLED,
        }


__all__ = ["HandoffRequest", "HandoffResponse", "HandoffStatus"]
