"""Handoff timeout — enforces the deadline attached to a `HandoffRequest`.

On expiry the manager returns a `HandoffResponse` with status=TIMEOUT and
records an audit entry. Timeout checks are cheap (timestamp comparison),
so the manager can call `check()` both before dispatch (reject expired)
and after dispatch (catch a slow handler).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from albra_seed.core.types import utc_now
from albra_seed.handoff.contract import HandoffRequest, HandoffResponse, HandoffStatus

# Audit sink type for timeout events.
TimeoutAuditSink = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class TimeoutAuditRecord:
    execution_id: str
    requester: str
    target: str
    deadline: datetime | None
    fired_at: datetime = field(default_factory=utc_now)
    message: str = "handoff deadline exceeded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "requester": self.requester,
            "target": self.target,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "fired_at": self.fired_at.isoformat(),
            "message": self.message,
            "status": HandoffStatus.TIMEOUT,
        }


class HandoffTimeout:
    """Enforces a single handoff's deadline."""

    def __init__(
        self,
        request: HandoffRequest,
        audit_sink: TimeoutAuditSink | None = None,
    ) -> None:
        self._request = request
        self._audit_sink = audit_sink
        self._fired = False
        self._record: TimeoutAuditRecord | None = None

    @property
    def request(self) -> HandoffRequest:
        return self._request

    def is_expired(self, *, now: datetime | None = None) -> bool:
        return self._request.is_expired(now=now)

    def remaining_seconds(self, *, now: datetime | None = None) -> float | None:
        if self._request.deadline is None:
            return None
        delta = (
            self._request.deadline - (now or utc_now())
        ).total_seconds()
        return max(0.0, delta)

    def fire(self, message: str | None = None) -> HandoffResponse:
        """Returns a TIMEOUT response and (optionally) records the audit
        event. Idempotent: subsequent calls return the same record."""
        if not self._fired:
            self._record = TimeoutAuditRecord(
                execution_id=self._request.execution_id,
                requester=self._request.requester,
                target=self._request.target,
                deadline=self._request.deadline,
                message=message or "handoff deadline exceeded",
            )
            self._fired = True
        return HandoffResponse.timeout(
            self._request.execution_id,
            responder=self._request.target,
            message=self._record.message if self._record else "timeout",
        )

    @property
    def fired(self) -> bool:
        return self._fired

    @property
    def audit_record(self) -> TimeoutAuditRecord | None:
        return self._record

    async def maybe_audit(self) -> None:
        """Push the audit record to the configured sink (if any and only
        if the timeout has been fired)."""
        if not self._fired or self._audit_sink is None or self._record is None:
            return
        try:
            await self._audit_sink(self._record.to_dict())
        except Exception:
            # Audit failures must NEVER break the handoff path.
            pass


__all__ = [
    "HandoffTimeout",
    "TimeoutAuditRecord",
    "TimeoutAuditSink",
]
