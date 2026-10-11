"""CircuitBreaker — CLOSED → OPEN → HALF_OPEN → CLOSED state machine.

A circuit breaker protects a downstream service from cascading failures:

  CLOSED     — calls flow through normally.
  OPEN       — calls fail fast with `CircuitBreakerOpenError`.
  HALF_OPEN  — a single trial call is permitted; success transitions
               back to CLOSED, failure returns to OPEN.

Transitions:
  CLOSED → OPEN      : after `failure_threshold` consecutive failures.
  OPEN  → HALF_OPEN  : after `reset_seconds` since opening.
  HALF_OPEN → CLOSED : on first success.
  HALF_OPEN → OPEN   : on first failure.

The breaker is generic: it wraps async callables. Specialization layers
register breakers per downstream (LLM provider, tool, plugin, ...).
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a call is attempted against an OPEN circuit."""

    def __init__(self, name: str, state: CircuitState) -> None:
        self.name = name
        self.state = state
        super().__init__(f"circuit '{name}' is {state.value}")


class CircuitBreaker:
    """Per-name circuit breaker with configurable thresholds."""

    def __init__(
        self,
        *,
        name: str = "default",
        failure_threshold: int = 5,
        reset_seconds: float = 30.0,
        half_open_max_calls: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self.half_open_max_calls = half_open_max_calls
        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._opened_at: float | None = None
        self._half_open_calls: int = 0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # State inspection
    # ------------------------------------------------------------------
    @property
    def state(self) -> CircuitState:
        # Auto-transition OPEN -> HALF_OPEN if reset_seconds elapsed.
        if (
            self._state == CircuitState.OPEN
            and self._opened_at is not None
            and (time.monotonic() - self._opened_at) >= self.reset_seconds
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_calls = 0
        return self._state

    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN

    # ------------------------------------------------------------------
    # Public recording API (manual)
    # ------------------------------------------------------------------
    def record_success(self) -> None:
        self._success_count += 1
        if self._state == CircuitState.HALF_OPEN:
            # Trial succeeded — close the circuit.
            self._reset_to_closed()

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._state == CircuitState.HALF_OPEN:
            # Trial failed — reopen.
            self._open()
            return
        if self._failure_count >= self.failure_threshold:
            self._open()

    # ------------------------------------------------------------------
    # Call API
    # ------------------------------------------------------------------
    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            current = self.state
            if current == CircuitState.OPEN:
                raise CircuitBreakerOpenError(self.name, current)
            if current == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitBreakerOpenError(self.name, CircuitState.HALF_OPEN)
                self._half_open_calls += 1
        try:
            value = await fn()
        except Exception as exc:  # noqa: BLE001
            self.record_failure()
            raise
        self.record_success()
        return value

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "failure_threshold": self.failure_threshold,
            "reset_seconds": self.reset_seconds,
            "opened_at": self._opened_at,
        }

    def reset(self) -> None:
        """Force-reset to CLOSED state."""
        self._reset_to_closed()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._half_open_calls = 0

    def _reset_to_closed(self) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._opened_at = None


__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "CircuitBreakerOpenError",
]
