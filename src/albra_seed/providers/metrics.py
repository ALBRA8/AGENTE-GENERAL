"""Per-provider metrics model.

Captures aggregate operational signals for a provider: call count,
error count, latency statistics, success rate. This is a pure data model;
it knows nothing about HOW providers produce completions, only WHAT
happened. No domain knowledge lives here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now


class ProviderMetrics(BaseModel):
    """Aggregate metrics for a single provider instance.

    Attributes
    ----------
    name:
        Provider identifier — matches `LLMProvider.name`.
    calls:
        Total number of `complete()` invocations attempted.
    successes:
        Subset of `calls` that returned without raising AND produced a
        non-empty AgentResponse.
    errors:
        Total number of calls that raised or returned an error payload.
    total_latency_ms:
        Cumulative latency across all calls. Divide by `calls` for mean.
    last_latency_ms:
        Latency of the most recent call. Useful for tail detection.
    last_error:
        String representation of the most recent error (if any).
    last_call_at:
        Timestamp of the most recent call attempt.
    started_at:
        When this metrics object was first created (provider init time).
    extra:
        Free-form per-provider extension bag (e.g. tokens-in, tokens-out).
    """

    name: str
    calls: int = 0
    successes: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0
    last_latency_ms: float = 0.0
    last_error: str | None = None
    last_call_at: datetime | None = None
    started_at: datetime = Field(default_factory=utc_now)
    extra: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Mutators — intended to be called by ProviderHealth or a wrapper.  #
    # ------------------------------------------------------------------ #

    def record_success(self, latency_ms: float) -> None:
        """Record a successful call."""
        self.calls += 1
        self.successes += 1
        self._record_latency(latency_ms)

    def record_error(self, latency_ms: float, error: str) -> None:
        """Record a failed call."""
        self.calls += 1
        self.errors += 1
        self.last_error = error
        self._record_latency(latency_ms)

    def reset(self) -> None:
        """Reset all counters (used on health recovery)."""
        self.calls = 0
        self.successes = 0
        self.errors = 0
        self.total_latency_ms = 0.0
        self.last_latency_ms = 0.0
        self.last_error = None
        self.last_call_at = None
        self.started_at = utc_now()

    # ------------------------------------------------------------------ #
    # Derived statistics                                                  #
    # ------------------------------------------------------------------ #

    @property
    def success_rate(self) -> float:
        """Success rate in [0.0, 1.0]. 1.0 when no calls have been made."""
        if self.calls == 0:
            return 1.0
        return self.successes / self.calls

    @property
    def error_rate(self) -> float:
        """Error rate in [0.0, 1.0]."""
        if self.calls == 0:
            return 0.0
        return self.errors / self.calls

    @property
    def mean_latency_ms(self) -> float:
        """Mean latency in milliseconds. 0.0 when no calls."""
        if self.calls == 0:
            return 0.0
        return self.total_latency_ms / self.calls

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of current metrics."""
        return {
            "name": self.name,
            "calls": self.calls,
            "successes": self.successes,
            "errors": self.errors,
            "success_rate": round(self.success_rate, 4),
            "error_rate": round(self.error_rate, 4),
            "mean_latency_ms": round(self.mean_latency_ms, 2),
            "last_latency_ms": round(self.last_latency_ms, 2),
            "last_error": self.last_error,
            "last_call_at": self.last_call_at.isoformat() if self.last_call_at else None,
            "started_at": self.started_at.isoformat(),
            "extra": dict(self.extra),
        }

    # ------------------------------------------------------------------ #
    # Internal                                                           #
    # ------------------------------------------------------------------ #

    def _record_latency(self, latency_ms: float) -> None:
        self.total_latency_ms += max(0.0, float(latency_ms))
        self.last_latency_ms = float(latency_ms)
        self.last_call_at = utc_now()


__all__ = ["ProviderMetrics"]
