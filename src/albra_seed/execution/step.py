"""TraceStep — a single phase entry in an ExecutionTrace.

Every step records the phase name, timing, inputs/outputs, the tool calls
and provider calls issued during the phase, any decisions made by the
policy/firewall layers, errors and warnings produced, and the evidence
identifiers captured. Steps are pydantic v2 models so they serialize to
JSON for audit / replay.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now


class TraceStep(BaseModel):
    """A single phase entry in an ExecutionTrace."""

    phase: str
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    duration_ms: float = 0.0

    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)

    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    provider_calls: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    evidence_ids: list[str] = Field(default_factory=list)

    status: str = "success"  # success | failure | timeout | skipped | blocked

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    @classmethod
    def start(cls, phase: str, *, inputs: dict[str, Any] | None = None) -> "TraceStep":
        return cls(phase=phase, inputs=dict(inputs or {}), started_at=utc_now())

    def finish(
        self,
        *,
        outputs: dict[str, Any] | None = None,
        status: str = "success",
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> "TraceStep":
        self.finished_at = utc_now()
        if self.started_at is not None and self.finished_at is not None:
            delta = (self.finished_at - self.started_at).total_seconds()
            self.duration_ms = delta * 1000.0
        if outputs is not None:
            self.outputs = dict(outputs)
        self.status = status
        if errors is not None:
            self.errors = list(errors)
        if warnings is not None:
            self.warnings = list(warnings)
        return self

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------
    def add_tool_call(self, tool_name: str, **info: Any) -> None:
        rec: dict[str, Any] = {"tool": tool_name}
        rec.update(info)
        self.tool_calls.append(rec)

    def add_provider_call(self, provider_name: str, **info: Any) -> None:
        rec: dict[str, Any] = {"provider": provider_name}
        rec.update(info)
        self.provider_calls.append(rec)

    def add_decision(self, decision: str, **info: Any) -> None:
        rec: dict[str, Any] = {"decision": decision}
        rec.update(info)
        self.decisions.append(rec)

    def add_error(self, error: str) -> None:
        self.errors.append(error)

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)

    def add_evidence(self, evidence_id: str) -> None:
        if evidence_id and evidence_id not in self.evidence_ids:
            self.evidence_ids.append(evidence_id)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = ["TraceStep"]
