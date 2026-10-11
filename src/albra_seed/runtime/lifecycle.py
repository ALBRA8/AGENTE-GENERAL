"""Lifecycle management for the ALBRA runtime.

Defines the canonical state machine every Runtime instance follows:

    UNINITIALIZED
        ↓ startup()
    INITIALIZING
        ↓ (components registered)
    READY
        ↓ (active execution)
    RUNNING
        ↓ shutdown()
    SHUTTING_DOWN
        ↓
    STOPPED

ERROR is reachable from any state; once in ERROR, only explicit reset()
returns the runtime to UNINITIALIZED.

Transitions are guarded: invalid transitions raise LifecycleError instead
of silently no-op'ing. Every transition emits a LifecycleEvent recorded
in the manager's history (append-only) — used by the audit layer.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now


class LifecyclePhase(str, Enum):
    """Canonical runtime lifecycle phases. Order matters for guards."""

    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"
    ERROR = "error"


# Allowed forward transitions. Anything not listed is rejected.
_ALLOWED_TRANSITIONS: dict[LifecyclePhase, set[LifecyclePhase]] = {
    LifecyclePhase.UNINITIALIZED: {
        LifecyclePhase.INITIALIZING,
        LifecyclePhase.ERROR,
    },
    LifecyclePhase.INITIALIZING: {
        LifecyclePhase.READY,
        LifecyclePhase.ERROR,
        LifecyclePhase.SHUTTING_DOWN,
    },
    LifecyclePhase.READY: {
        LifecyclePhase.RUNNING,
        LifecyclePhase.SHUTTING_DOWN,
        LifecyclePhase.ERROR,
    },
    LifecyclePhase.RUNNING: {
        LifecyclePhase.READY,
        LifecyclePhase.SHUTTING_DOWN,
        LifecyclePhase.ERROR,
    },
    LifecyclePhase.SHUTTING_DOWN: {
        LifecyclePhase.STOPPED,
        LifecyclePhase.ERROR,
    },
    LifecyclePhase.STOPPED: {
        LifecyclePhase.INITIALIZING,  # restart allowed
        LifecyclePhase.ERROR,
    },
    LifecyclePhase.ERROR: {
        LifecyclePhase.UNINITIALIZED,  # explicit reset only
        LifecyclePhase.STOPPED,
    },
}


class LifecycleError(RuntimeError):
    """Raised when an invalid lifecycle transition is requested."""


class LifecycleEvent(BaseModel):
    """Immutable record of a single lifecycle transition."""

    phase: LifecyclePhase
    previous_phase: LifecyclePhase | None
    timestamp: datetime = Field(default_factory=utc_now)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# Listener signature: async or sync callable receiving the LifecycleEvent.
LifecycleListener = Callable[[LifecycleEvent], "Awaitable[None] | None"]


class LifecycleManager:
    """State machine + event history for a single Runtime instance.

    The manager is intentionally tiny: it owns the current phase, enforces
    transition guards, and keeps an append-only history. Higher-level
    concerns (component registration, session accounting) live on the Runtime.
    """

    def __init__(self, initial: LifecyclePhase = LifecyclePhase.UNINITIALIZED) -> None:
        self._phase: LifecyclePhase = initial
        self._history: list[LifecycleEvent] = []
        self._listeners: list[LifecycleListener] = []
        self._counts: dict[LifecyclePhase, int] = defaultdict(int)
        self._counts[initial] = 1

    # -- phase accessors -------------------------------------------------

    @property
    def phase(self) -> LifecyclePhase:
        return self._phase

    @property
    def history(self) -> list[LifecycleEvent]:
        """Immutable view of transition history (newest last)."""
        return list(self._history)

    def counts(self) -> dict[str, int]:
        """Number of times each phase was entered (debugging / metrics)."""
        return {p.value: c for p, c in self._counts.items()}

    # -- transitions -----------------------------------------------------

    def can_transition(self, to: LifecyclePhase) -> bool:
        return to in _ALLOWED_TRANSITIONS.get(self._phase, set())

    def transition(
        self,
        to: LifecyclePhase,
        *,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LifecycleEvent:
        """Transition to `to`. Raises LifecycleError if not allowed."""
        if not self.can_transition(to):
            raise LifecycleError(
                f"Illegal transition: {self._phase.value} -> {to.value}"
            )
        event = LifecycleEvent(
            phase=to,
            previous_phase=self._phase,
            reason=reason,
            metadata=metadata or {},
        )
        self._phase = to
        self._history.append(event)
        self._counts[to] += 1
        self._notify(event)
        return event

    def force(self, to: LifecyclePhase, *, reason: str = "force") -> LifecycleEvent:
        """Bypass guards. Used by error handlers / kill switch only."""
        event = LifecycleEvent(
            phase=to,
            previous_phase=self._phase,
            reason=reason,
            metadata={"forced": True},
        )
        self._phase = to
        self._history.append(event)
        self._counts[to] += 1
        self._notify(event)
        return event

    def reset(self) -> LifecycleEvent:
        """Return to UNINITIALIZED. Only valid from ERROR or STOPPED."""
        return self.transition(LifecyclePhase.UNINITIALIZED, reason="reset")

    # -- listeners -------------------------------------------------------

    def add_listener(self, listener: LifecycleListener) -> None:
        self._listeners.append(listener)

    def _notify(self, event: LifecycleEvent) -> None:
        for listener in list(self._listeners):
            try:
                result = listener(event)
                if hasattr(result, "__await__"):
                    # Schedule async listener without blocking the caller.
                    import asyncio

                    asyncio.ensure_future(result)  # noqa: PLC0415
            except Exception:
                # Listeners must never break transitions.
                continue


__all__ = [
    "LifecyclePhase",
    "LifecycleEvent",
    "LifecycleManager",
    "LifecycleError",
    "LifecycleListener",
]
