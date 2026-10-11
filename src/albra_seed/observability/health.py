"""HealthMonitor — tracks per-component health state.

Components register themselves with `register(name)`; subsystems then
report state changes via `report(name, state, message=None)`.

HealthMonitor does NOT itself probe components — that's the Doctor's
job. HealthMonitor only records what subsystems have *reported*. This
keeps the monitor O(1) per report and avoids time-based ambiguity
("is the last report stale?").
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from albra_seed.core.types import HealthState


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HealthReport:
    """Latest health report for a single component."""

    __slots__ = ("name", "state", "message", "reported_at")

    def __init__(self, name: str, state: HealthState, message: str | None = None) -> None:
        self.name = name
        self.state = state
        self.message = message
        self.reported_at: datetime = _utcnow()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "message": self.message,
            "reported_at": self.reported_at.isoformat(),
        }


# Mapping from HealthState to a comparable severity rank — higher = worse.
_STATE_RANK: dict[HealthState, int] = {
    HealthState.HEALTHY: 0,
    HealthState.WARNING: 1,
    HealthState.DEGRADED: 2,
    HealthState.NOT_VERIFIED: 3,
    HealthState.NOT_CONFIGURED: 4,
    HealthState.BLOCKED: 5,
    HealthState.ERROR: 6,
}


class HealthMonitor:
    """Per-component health registry with roll-up snapshot."""

    def __init__(self) -> None:
        self._reports: dict[str, HealthReport] = {}
        self._lock = threading.Lock()

    def register(self, name: str) -> None:
        """Register a component (default state: NOT_CONFIGURED)."""
        with self._lock:
            if name not in self._reports:
                self._reports[name] = HealthReport(name, HealthState.NOT_CONFIGURED)

    def report(self, name: str, state: HealthState, message: str | None = None) -> None:
        with self._lock:
            self._reports[name] = HealthReport(name, state, message)

    def get(self, name: str) -> HealthReport | None:
        with self._lock:
            return self._reports.get(name)

    def list_components(self) -> list[str]:
        with self._lock:
            return sorted(self._reports.keys())

    def overall_state(self) -> HealthState:
        """Return the worst reported state across all components.

        If no components are registered, returns NOT_CONFIGURED.
        """
        with self._lock:
            if not self._reports:
                return HealthState.NOT_CONFIGURED
            worst = HealthState.HEALTHY
            for r in self._reports.values():
                if _STATE_RANK[r.state] > _STATE_RANK[worst]:
                    worst = r.state
            return worst

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            components = {name: r.to_dict() for name, r in self._reports.items()}
        return {
            "overall_state": self.overall_state().value,
            "components": components,
            "component_count": len(components),
        }

    def clear(self) -> None:
        with self._lock:
            self._reports.clear()


__all__ = ["HealthMonitor", "HealthReport"]
