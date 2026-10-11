"""ReadinessProbe — True only when all critical components are healthy.

A "critical" component is one registered as such; the probe returns
True iff every critical component has reported HEALTHY (or WARNING —
which is considered serviceable but degraded).

Components in NOT_CONFIGURED / NOT_VERIFIED / BLOCKED / ERROR state
cause readiness=False.
"""

from __future__ import annotations

from typing import Iterable

from albra_seed.core.types import HealthState
from albra_seed.observability.health import HealthMonitor


# States considered "ready" for serving traffic. WARNING counts as
# ready because the component is still operational, just degraded.
_READY_STATES: frozenset[HealthState] = frozenset({
    HealthState.HEALTHY,
    HealthState.WARNING,
})


class ReadinessProbe:
    """Determines if the agent is ready to serve requests.

    Usage:
        probe = ReadinessProbe(monitor)
        probe.mark_critical("provider_router")
        probe.mark_critical("memory_store")
        if probe.ready():
            serve()
    """

    def __init__(self, monitor: HealthMonitor) -> None:
        self._monitor = monitor
        self._critical: set[str] = set()

    def mark_critical(self, name: str) -> None:
        self._critical.add(name)
        # Ensure the component exists in the monitor with NOT_CONFIGURED
        # default so it shows up in snapshots.
        self._monitor.register(name)

    def unmark_critical(self, name: str) -> None:
        self._critical.discard(name)

    def critical_components(self) -> list[str]:
        return sorted(self._critical)

    def ready(self) -> bool:
        """True iff every critical component is in a READY state."""
        if not self._critical:
            # No critical components declared — by convention NOT ready,
            # because the operator hasn't told us what to check.
            return False
        for name in self._critical:
            report = self._monitor.get(name)
            if report is None:
                return False
            if report.state not in _READY_STATES:
                return False
        return True

    def not_ready_reasons(self) -> list[dict[str, str]]:
        """Return a list of {component, state, message} for each
        critical component that is not ready."""
        reasons: list[dict[str, str]] = []
        for name in self._critical:
            report = self._monitor.get(name)
            if report is None:
                reasons.append({"component": name, "state": "missing", "message": "not registered"})
                continue
            if report.state not in _READY_STATES:
                reasons.append({
                    "component": name,
                    "state": report.state.value,
                    "message": report.message or "",
                })
        return reasons

    def snapshot(self) -> dict[str, object]:
        return {
            "ready": self.ready(),
            "critical_components": list(self._critical),
            "not_ready_reasons": self.not_ready_reasons(),
        }


__all__ = ["ReadinessProbe"]
