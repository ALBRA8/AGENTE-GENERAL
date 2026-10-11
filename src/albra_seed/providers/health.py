"""Provider health state tracking.

`ProviderHealth` is the per-provider health state machine. It wraps a
`ProviderMetrics` instance and exposes a single `HealthState` value
(`HEALTHY` / `DEGRADED` / `UNHEALTHY` / `UNKNOWN`). Health transitions
are derived from metrics — never set arbitrarily. The state machine is
intentionally simple: the goal is to gate routing decisions, not to
implement a full circuit-breaker (see `ProviderFallback` for chains).

No domain knowledge lives here.
"""

from __future__ import annotations

from typing import Any

from albra_seed.core.types import HealthState, utc_now
from albra_seed.providers.metrics import ProviderMetrics

# Default thresholds. A provider is considered degraded when its success
# rate drops below `DEGRADED_SUCCESS_RATE`, unhealthy when below
# `UNHEALTHY_SUCCESS_RATE`, or when it accumulates `UNHEALTHY_ERROR_STREAK`
# consecutive errors.
DEGRADED_SUCCESS_RATE: float = 0.9
UNHEALTHY_SUCCESS_RATE: float = 0.5
UNHEALTHY_ERROR_STREAK: int = 5
HEALTH_MIN_CALLS: int = 3  # don't judge a provider before this many calls


class ProviderHealth:
    """Per-provider health tracker.

    Tracks metrics + derived HealthState. The HealthState is recomputed
    from metrics on each `state` access — there is no cached state to
    invalidate. `mark_healthy` / `mark_unhealthy` allow external override
    (e.g. on circuit-breaker recovery); use sparingly.
    """

    def __init__(
        self,
        name: str,
        *,
        degraded_success_rate: float = DEGRADED_SUCCESS_RATE,
        unhealthy_success_rate: float = UNHEALTHY_SUCCESS_RATE,
        unhealthy_error_streak: int = UNHEALTHY_ERROR_STREAK,
        health_min_calls: int = HEALTH_MIN_CALLS,
    ) -> None:
        self.name: str = name
        self.metrics: ProviderMetrics = ProviderMetrics(name=name)
        self._override: HealthState | None = None
        self._override_reason: str | None = None
        self._override_at: Any = None
        self._error_streak: int = 0
        self.degraded_success_rate: float = degraded_success_rate
        self.unhealthy_success_rate: float = unhealthy_success_rate
        self.unhealthy_error_streak: int = unhealthy_error_streak
        self.health_min_calls: int = health_min_calls

    # ------------------------------------------------------------------ #
    # Recording                                                          #
    # ------------------------------------------------------------------ #

    def record_success(self, latency_ms: float) -> None:
        self.metrics.record_success(latency_ms)
        self._error_streak = 0
        # Clear any manual override on the next success.
        if self._override == HealthState.DEGRADED:
            self._clear_override()

    def record_error(self, latency_ms: float, error: str) -> None:
        self.metrics.record_error(latency_ms, error)
        self._error_streak += 1

    # ------------------------------------------------------------------ #
    # Derived state                                                      #
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> HealthState:
        """Return the current derived health state."""
        if self._override is not None:
            return self._override
        if self.metrics.calls < self.health_min_calls:
            return HealthState.HEALTHY  # optimistic default
        if self._error_streak >= self.unhealthy_error_streak:
            return HealthState.ERROR
        if self.metrics.success_rate < self.unhealthy_success_rate:
            return HealthState.ERROR
        if self.metrics.success_rate < self.degraded_success_rate:
            return HealthState.DEGRADED
        return HealthState.HEALTHY

    @property
    def is_available(self) -> bool:
        """True if the provider can be routed to right now."""
        return self.state in {HealthState.HEALTHY, HealthState.DEGRADED}

    # ------------------------------------------------------------------ #
    # Manual overrides                                                   #
    # ------------------------------------------------------------------ #

    def mark_healthy(self, reason: str = "manual") -> None:
        self._override = HealthState.HEALTHY
        self._override_reason = reason
        self._override_at = utc_now()
        self._error_streak = 0
        self.metrics.last_error = None

    def mark_unhealthy(self, reason: str = "manual") -> None:
        self._override = HealthState.ERROR
        self._override_reason = reason
        self._override_at = utc_now()

    def _clear_override(self) -> None:
        self._override = None
        self._override_reason = None
        self._override_at = None

    # ------------------------------------------------------------------ #
    # Snapshot                                                          #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "available": self.is_available,
            "override": self._override.value if self._override else None,
            "override_reason": self._override_reason,
            "error_streak": self._error_streak,
            "metrics": self.metrics.snapshot(),
        }


__all__ = ["ProviderHealth"]
