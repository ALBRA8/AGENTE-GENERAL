"""ExecutionTrace — detailed trace of a single execution.

An ExecutionTrace captures the full lifecycle of one execution: its
identifier, correlation id, the ordered list of TraceStep objects, the
overall status, and timing. It can be serialized to JSON for audit,
storage, and replay.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import ExecutionStatus, utc_now, new_id
from albra_seed.execution.step import TraceStep


class ExecutionTrace(BaseModel):
    """Detailed trace of a single execution."""

    execution_id: str = Field(default_factory=lambda: new_id("exec"))
    correlation_id: str = Field(default_factory=lambda: new_id("corr"))
    parent_execution_id: str | None = None
    agent_id: str = "unknown"
    task: str = ""

    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    duration_ms: float = 0.0

    steps: list[TraceStep] = Field(default_factory=list)

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def begin_step(self, phase: str, *, inputs: dict[str, Any] | None = None) -> TraceStep:
        step = TraceStep.start(phase, inputs=inputs)
        self.steps.append(step)
        return step

    def finish(
        self,
        *,
        status: ExecutionStatus | str | None = None,
    ) -> "ExecutionTrace":
        self.finished_at = utc_now()
        if self.started_at is not None:
            self.duration_ms = (self.finished_at - self.started_at).total_seconds() * 1000.0
        if status is not None:
            self.status = ExecutionStatus(status) if isinstance(status, str) else status
        # Roll up per-step errors/warnings/evidence.
        for step in self.steps:
            for e in step.errors:
                if e not in self.errors:
                    self.errors.append(e)
            for w in step.warnings:
                if w not in self.warnings:
                    self.warnings.append(w)
            for ev in step.evidence_ids:
                if ev not in self.evidence_ids:
                    self.evidence_ids.append(ev)
        return self

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def step(self, phase: str) -> TraceStep | None:
        for s in self.steps:
            if s.phase == phase:
                return s
        return None

    @property
    def is_success(self) -> bool:
        return self.status == ExecutionStatus.SUCCEEDED

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMEOUT,
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExecutionTrace":
        steps_data = data.get("steps") or []
        steps = [TraceStep.model_validate(s) for s in steps_data]
        return cls(
            execution_id=data.get("execution_id", new_id("exec")),
            correlation_id=data.get("correlation_id", new_id("corr")),
            parent_execution_id=data.get("parent_execution_id"),
            agent_id=data.get("agent_id", "unknown"),
            task=data.get("task", ""),
            status=ExecutionStatus(data.get("status", ExecutionStatus.PENDING.value))
            if isinstance(data.get("status"), str)
            else ExecutionStatus.PENDING,
            started_at=data.get("started_at", utc_now()),
            finished_at=data.get("finished_at"),
            duration_ms=float(data.get("duration_ms", 0.0)),
            steps=steps,
            errors=list(data.get("errors", [])),
            warnings=list(data.get("warnings", [])),
            evidence_ids=list(data.get("evidence_ids", [])),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_json(cls, raw: str) -> "ExecutionTrace":
        return cls.from_dict(json.loads(raw))


__all__ = ["ExecutionTrace"]
