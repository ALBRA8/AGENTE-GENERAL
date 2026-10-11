"""DiagnosticReport — per-component diagnostic result.

Each component is described along TWO orthogonal axes:

  1. `status` (HealthState):
       HEALTHY / DEGRADED / WARNING / ERROR / BLOCKED / NOT_CONFIGURED /
       NOT_VERIFIED.

  2. `implementation_status` (ImplementationStatus):
       IMPLEMENTED / MOCK / PLACEHOLDER / EXTERNAL_DEPENDENCY /
       NOT_CONFIGURED / NOT_VERIFIED.

CRITICAL RULE (enforced by Doctor, not here): a component MUST NOT be
reported VERIFIED/HEALTHY+IMPLEMENTED if only structurally implemented.
The doctor MUST actually RUN the check (probe the runtime / import the
module / send a test message) before declaring a component healthy.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import HealthState, utc_now


class ImplementationStatus(str, Enum):
    """How completely a component is wired up, independent of health."""

    IMPLEMENTED = "implemented"
    MOCK = "mock"
    PLACEHOLDER = "placeholder"
    EXTERNAL_DEPENDENCY = "external_dependency"
    NOT_CONFIGURED = "not_configured"
    NOT_VERIFIED = "not_verified"


class ComponentDiagnostic(BaseModel):
    """Per-component diagnostic entry."""

    name: str
    status: HealthState = HealthState.NOT_VERIFIED
    implementation_status: ImplementationStatus = ImplementationStatus.NOT_VERIFIED
    details: str = ""
    evidence: list[str] = Field(default_factory=list)
    checked_at: str = Field(default_factory=lambda: utc_now().isoformat())
    duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "implementation_status": self.implementation_status.value,
            "details": self.details,
            "evidence": list(self.evidence),
            "checked_at": self.checked_at,
            "duration_ms": self.duration_ms,
        }


class DiagnosticReport(BaseModel):
    """Aggregate diagnostic report across all checked components.

    `components` is a flat list; `summary` rolls up counts per status /
    implementation_status for quick scanning.
    """

    generated_at: str = Field(default_factory=lambda: utc_now().isoformat())
    components: list[ComponentDiagnostic] = Field(default_factory=list)
    overall_status: HealthState = HealthState.NOT_VERIFIED

    def add(self, diag: ComponentDiagnostic) -> None:
        self.components.append(diag)
        self._recompute_overall()

    def get(self, name: str) -> ComponentDiagnostic | None:
        for c in self.components:
            if c.name == name:
                return c
        return None

    def summary(self) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        impl_counts: dict[str, int] = {}
        for c in self.components:
            s = c.status.value
            status_counts[s] = status_counts.get(s, 0) + 1
            i = c.implementation_status.value
            impl_counts[i] = impl_counts.get(i, 0) + 1
        return {
            "overall_status": self.overall_status.value,
            "component_count": len(self.components),
            "status_counts": status_counts,
            "implementation_counts": impl_counts,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "overall_status": self.overall_status.value,
            "summary": self.summary(),
            "components": [c.to_dict() for c in self.components],
        }

    def _recompute_overall(self) -> None:
        rank = {
            HealthState.HEALTHY: 0,
            HealthState.WARNING: 1,
            HealthState.DEGRADED: 2,
            HealthState.NOT_VERIFIED: 3,
            HealthState.NOT_CONFIGURED: 4,
            HealthState.BLOCKED: 5,
            HealthState.ERROR: 6,
        }
        worst = HealthState.HEALTHY
        for c in self.components:
            if rank[c.status] > rank[worst]:
                worst = c.status
        self.overall_status = worst


__all__ = ["ImplementationStatus", "ComponentDiagnostic", "DiagnosticReport"]
