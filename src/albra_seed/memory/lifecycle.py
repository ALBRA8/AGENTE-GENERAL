"""Memory lifecycle tracking — creation, modification, archival, deletion.

Lightweight per-record lifecycle so audit and consolidation can replay history.
No external storage required; integrates with the audit subsystem via events.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now


class MemoryLifecycleState(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    ACCESSED = "accessed"
    CONSOLIDATED = "consolidated"
    ARCHIVED = "archived"
    DELETED = "deleted"
    FORGOTTEN = "forgotten"


class MemoryLifecycleEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("mle"))
    record_id: str
    state: MemoryLifecycleState
    occurred_at: Any = Field(default_factory=utc_now)
    by: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryLifecycle:
    """Tracks the lifecycle of a memory record.

    Use `record_*` methods to append events. `history(record_id)` returns the
    ordered event list. `current_state(record_id)` returns the latest state.
    """

    def __init__(self) -> None:
        self._events: dict[str, list[MemoryLifecycleEvent]] = {}

    def record_created(self, record_id: str, *, by: str | None = None) -> MemoryLifecycleEvent:
        return self._append(record_id, MemoryLifecycleState.CREATED, by=by)

    def record_modified(
        self, record_id: str, *, by: str | None = None, reason: str | None = None
    ) -> MemoryLifecycleEvent:
        return self._append(record_id, MemoryLifecycleState.MODIFIED, by=by, reason=reason)

    def record_accessed(self, record_id: str, *, by: str | None = None) -> MemoryLifecycleEvent:
        return self._append(record_id, MemoryLifecycleState.ACCESSED, by=by)

    def record_consolidated(
        self, record_id: str, *, reason: str | None = None
    ) -> MemoryLifecycleEvent:
        return self._append(record_id, MemoryLifecycleState.CONSOLIDATED, reason=reason)

    def record_archived(
        self, record_id: str, *, reason: str | None = None
    ) -> MemoryLifecycleEvent:
        return self._append(record_id, MemoryLifecycleState.ARCHIVED, reason=reason)

    def record_deleted(
        self, record_id: str, *, reason: str | None = None
    ) -> MemoryLifecycleEvent:
        return self._append(record_id, MemoryLifecycleState.DELETED, reason=reason)

    def record_forgotten(
        self, record_id: str, *, reason: str | None = None
    ) -> MemoryLifecycleEvent:
        return self._append(record_id, MemoryLifecycleState.FORGOTTEN, reason=reason)

    def _append(
        self,
        record_id: str,
        state: MemoryLifecycleState,
        *,
        by: str | None = None,
        reason: str | None = None,
    ) -> MemoryLifecycleEvent:
        ev = MemoryLifecycleEvent(
            record_id=record_id, state=state, by=by, reason=reason
        )
        self._events.setdefault(record_id, []).append(ev)
        return ev

    def history(self, record_id: str) -> list[MemoryLifecycleEvent]:
        return list(self._events.get(record_id, []))

    def current_state(self, record_id: str) -> MemoryLifecycleState | None:
        events = self._events.get(record_id, [])
        return events[-1].state if events else None

    def is_alive(self, record_id: str) -> bool:
        state = self.current_state(record_id)
        if state is None:
            return False
        return state not in (
            MemoryLifecycleState.DELETED,
            MemoryLifecycleState.FORGOTTEN,
            MemoryLifecycleState.ARCHIVED,
        )
