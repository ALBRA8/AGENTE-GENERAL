"""NORMAL AuditLog — structured JSONL audit log, append-only.

This is the "human-readable / operator-facing" audit log. It records
*what happened* in a structured, queryable form. It is NOT tamper-
evident — for tamper-evidence use `TamperEvidentAuditLog`.

Both logs may be used simultaneously (and SHOULD be, in production):
  * Normal log: high-volume, easy to query, used for dashboards/SIEM.
  * Tamper-evident log: lower-volume, hash-chained, used for compliance.

The AuditLog itself is backend-agnostic — pass any `AuditStore` (in-
memory, file, SQLite). It enforces append-only semantics by exposing
no mutation API beyond `append`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from albra_seed.audit.entry import AuditEntry
from albra_seed.audit.store import AuditStore, InMemoryAuditStore
from albra_seed.context.correlation import CorrelationTracker


class AuditLog:
    """NORMAL structured audit log.

    Append-only. Records universal audit fields; domain-specific
    extensions live in `metadata`.

    Usage:
        log = AuditLog()
        log.record(action="tool.invoke", actor="agent",
                   tool="weather.get", result="success")
        for e in log.filter_by_correlation_id(corr_id):
            print(e)
    """

    def __init__(self, store: AuditStore | None = None) -> None:
        self._store = store or InMemoryAuditStore()

    @property
    def store(self) -> AuditStore:
        return self._store

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def record(
        self,
        *,
        action: str,
        actor: str | None = None,
        tool: str | None = None,
        provider: str | None = None,
        permission: str | None = None,
        approval_id: str | None = None,
        result: str = "success",
        error: str | None = None,
        correlation_id: str | None = None,
        execution_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """Record an audit entry (synchronous append to the store)."""
        # Auto-fill correlation_id / execution_id from context if not given.
        if correlation_id is None:
            correlation_id = CorrelationTracker.current_correlation_id()
        if execution_id is None:
            execution_id = CorrelationTracker.current_execution_id()
        entry = AuditEntry(
            action=action,
            actor=actor,
            tool=tool,
            provider=provider,
            permission=permission,
            approval_id=approval_id,
            result=result,
            error=error,
            correlation_id=correlation_id,
            execution_id=execution_id,
            metadata=metadata or {},
        )
        self._store.append(entry)
        return entry

    async def record_async(self, **kwargs: Any) -> AuditEntry:
        """Async variant — defers the append to a worker thread so the
        caller is never blocked by I/O on a file/sqlite store."""
        loop = asyncio.get_running_loop()
        # All kwargs are immutable enough for thread hand-off.
        return await loop.run_in_executor(None, lambda: self.record(**kwargs))

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------
    def all(self) -> list[AuditEntry]:
        return self._store.all()

    def filter_by_correlation_id(self, correlation_id: str) -> list[AuditEntry]:
        return self._store.filter(correlation_id=correlation_id)

    def filter_by_execution_id(self, execution_id: str) -> list[AuditEntry]:
        return self._store.filter(execution_id=execution_id)

    def filter_by_actor(self, actor: str) -> list[AuditEntry]:
        return self._store.filter(actor=actor)

    def filter(
        self,
        *,
        correlation_id: str | None = None,
        execution_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        result: str | None = None,
    ) -> list[AuditEntry]:
        return self._store.filter(
            correlation_id=correlation_id,
            execution_id=execution_id,
            actor=actor,
            action=action,
            result=result,
        )

    def count(self) -> int:
        if hasattr(self._store, "count"):
            return self._store.count()  # type: ignore[no-any-return]
        return len(self._store.all())


__all__ = ["AuditLog"]
