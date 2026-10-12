"""MemoryManager — high-level orchestrator over a MemoryStore.

Wraps a concrete MemoryStore and adds:
    - remember()  : write a memory with auto-attached provenance + lifecycle
    - recall()    : query memories within an isolation bucket
    - forget()    : delete a memory, write a tombstone, optionally emit an
                   audit event via the configured `audit_sink`
    - consolidate(): promote episodic → semantic when policy conditions met

Strict isolation is enforced on every call.

FACT-rule integrity is delegated to `MemoryProvenance.validate_fact` — the
SINGLE source of truth. This module does NOT re-implement fact validation.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Union

from albra_seed.core.base import MemoryRecord, MemoryStore
from albra_seed.core.types import MemoryType, new_id, utc_now
from albra_seed.memory.consolidation import ConsolidationPolicy, ConsolidationResult
from albra_seed.memory.isolator import MemoryIsolator
from albra_seed.memory.lifecycle import MemoryLifecycle
from albra_seed.memory.provenance import MemoryProvenance

# An audit sink receives a single dict per auditable event. It may be a sync
# callable or an async callable; both are supported. None disables auditing.
AuditSink = Callable[[dict[str, Any]], Union[None, Awaitable[None]]]


class MemoryManager:
    """High-level orchestrator over a MemoryStore."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        provenance: MemoryProvenance | None = None,
        lifecycle: MemoryLifecycle | None = None,
        consolidation: ConsolidationPolicy | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.store = store
        self.provenance = provenance or MemoryProvenance()
        self.lifecycle = lifecycle or MemoryLifecycle()
        self.consolidation = consolidation or ConsolidationPolicy()
        self._audit_sink: AuditSink | None = audit_sink

    async def remember(
        self,
        *,
        agent_id: str,
        content: Any,
        memory_type: MemoryType = MemoryType.EPISODIC,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        confidence: float = 0.0,
        source: str = "agent",
        source_type: str = "agent",
        evidence_ids: list[str] | None = None,
        execution_id: str | None = None,
        correlation_id: str | None = None,
        is_fact: bool = False,
    ) -> MemoryRecord:
        # Build the record first so the unified FACT rule can see all fields.
        record = MemoryRecord(
            record_id=new_id("mem"),
            agent_id=agent_id,
            domain=domain,
            user_id=user_id,
            context_id=context_id,
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            source=source,
            source_type=source_type,
            evidence_ids=list(evidence_ids or []),
            execution_id=execution_id,
            correlation_id=correlation_id,
            is_fact=is_fact,
            created_at=utc_now(),
            modified_at=utc_now(),
        )

        # Validate against the UNIVERSAL FACT rule — single source of truth.
        # `MemoryProvenance.validate_fact` enforces: evidence_ids non-empty,
        # source_type in {tool, external, verified_provider}, confidence >= 0.9.
        is_valid, reason = MemoryProvenance.validate_fact(record)
        if not is_valid:
            raise ValueError(f"FACT memory rejected: {reason}")

        record, _prov = self.provenance.attach(record)
        # Enforce composite isolation key is consistent on the record.
        MemoryIsolator.key_for(
            record.agent_id,
            record.domain,
            record.user_id,
            record.context_id,
        )
        await self.store.write(record)
        self.lifecycle.record_created(record.record_id, by=source)
        return record

    async def recall(
        self,
        *,
        agent_id: str,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        memory_type: MemoryType | None = None,
        execution_id: str | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        records = await self.store.query(
            agent_id=agent_id,
            domain=domain,
            user_id=user_id,
            context_id=context_id,
            memory_type=memory_type,
            execution_id=execution_id,
            limit=limit,
        )
        for r in records:
            self.lifecycle.record_accessed(r.record_id)
        return records

    async def forget(
        self,
        record_id: str,
        *,
        reason: str | None = None,
        by: str | None = None,
    ) -> bool:
        """Delete a memory record and write a tombstone to the underlying store.

        The tombstone contains: ``record_id``, timestamp, ``by`` (who deleted),
        ``reason`` (why). Tombstones are persisted in SQLite stores; in-memory
        stores keep them until process restart.

        If an ``audit_sink`` was configured on the MemoryManager, a structured
        audit event is also emitted:
        ``{"action": "memory.forget", "record_id": ..., "by": ..., "reason": ...}``.
        The audit sink is OPTIONAL — when ``None`` (default) no audit event is
        emitted and CORE_ONLY mode is unchanged.

        Lifecycle events (in-memory only, lost on restart) also record the
        deletion with the supplied ``reason``.

        Returns ``True`` if the record was deleted, ``False`` if it did not
        exist.
        """
        record = await self.store.read(record_id)
        if record is None:
            return False
        ok = await self.store.delete(record_id, by=by, reason=reason)
        if ok:
            self.lifecycle.record_deleted(record_id, reason=reason)
            self.lifecycle.record_forgotten(record_id, reason=reason)
            await self._maybe_audit(
                {
                    "action": "memory.forget",
                    "record_id": record_id,
                    "by": by,
                    "reason": reason,
                }
            )
        return ok

    async def consolidate(
        self, record_id: str, *, by: str = "consolidator"
    ) -> ConsolidationResult:
        record = await self.store.read(record_id)
        if record is None:
            return ConsolidationResult(
                promoted=False,
                record_id=record_id,
                old_type=MemoryType.WORKING,
                reason="record not found",
            )
        # Snapshot the fields the policy may mutate, so we can roll back if
        # the FACT rule rejects the post-promotion record.
        old_type = record.memory_type
        old_version = record.version
        result = self.consolidation.consolidate(record)
        if result.promoted:
            # Re-validate the FACT rule before persisting — consolidation
            # never sets is_fact=True (see ConsolidationPolicy), but if the
            # caller hand-edited the record this guard still keeps us safe.
            is_valid, why = MemoryProvenance.validate_fact(record)
            if not is_valid:
                # Roll back in-place mutation so the store record stays
                # consistent with what we return.
                record.memory_type = old_type
                record.version = old_version
                return ConsolidationResult(
                    promoted=False,
                    record_id=record_id,
                    old_type=old_type,
                    reason=f"FACT rule violated: {why}",
                )
            await self.store.write(record)
            self.lifecycle.record_consolidated(record_id, reason=result.reason)
            self.lifecycle.record_modified(record_id, by=by, reason=result.reason)
        return result

    async def cleanup_working(
        self,
        *,
        agent_id: str,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        execution_id: str | None = None,
    ) -> int:
        """Delete all WORKING memories matching the isolation dimensions.

        Used by the agent loop at the end of execution (success, error, or
        cancel) to garbage-collect transient working memory.

        Isolation is enforced by the underlying store's ``query()`` against
        the composite key ``(agent_id, domain, user_id, context_id)``. When
        ``execution_id`` is provided, only WORKING records whose
        ``execution_id`` matches are deleted — so a long-lived context can
        carry WORKING memory from unrelated past executions and those stay
        untouched.

        The ``execution_id`` filter is applied AT THE STORE LEVEL (in the
        SQL query / in-memory scan), NOT as a Python post-filter on the
        returned records. This matters at scale: if a single execution has
        >1000 WORKING records, OR if 1500 WORKING records exist across
        executions and the target execution's records are the OLDEST (sorted
        last by ``created_at DESC``), a Python post-filter on a
        ``limit=1000`` query would MISS the target records entirely. By
        filtering at the store level we ensure all matching records are
        returned regardless of how the store orders them.

        Delegates to ``store.delete()`` which writes a tombstone on SQLite
        (consistent with ``forget()``) — never a physical wipe. Lifecycle
        events are recorded so retrieval (``MemoryRetriever``) and audit
        can replay the history.

        Returns the number of records deleted.
        """
        # Use execution_id filter at the store level — no Python post-filtering.
        # limit is a generous upper bound so we don't silently truncate a
        # large execution's WORKING set. If a future caller exceeds this
        # bound, the query simply returns the first 10_000; pagination can
        # be added at that point. None execution_id → no filter (returns
        # all matching WORKING records up to limit).
        records = await self.store.query(
            agent_id=agent_id,
            domain=domain,
            user_id=user_id,
            context_id=context_id,
            memory_type=MemoryType.WORKING,
            execution_id=execution_id,
            limit=10000,
        )
        deleted = 0
        for r in records:
            ok = await self.store.delete(
                r.record_id, reason="working_cleanup"
            )
            if ok:
                deleted += 1
                self.lifecycle.record_deleted(r.record_id, reason="working_cleanup")
        return deleted

    async def get(self, record_id: str) -> MemoryRecord | None:
        record = await self.store.read(record_id)
        if record is not None:
            self.lifecycle.record_accessed(record_id)
        return record

    async def count(
        self, *, agent_id: str, domain: str = "default"
    ) -> int:
        return await self.store.count(agent_id=agent_id, domain=domain)

    async def _maybe_audit(self, event: dict[str, Any]) -> None:
        """Forward an audit event to the configured sink, if any.

        Supports both sync and async sinks. Exceptions raised by an async sink
        are propagated; exceptions from a sync sink are also propagated (the
        caller decides whether to swallow). When no sink is configured, this
        is a no-op — CORE_ONLY mode is unaffected.
        """
        sink = self._audit_sink
        if sink is None:
            return
        result = sink(event)
        if isinstance(result, Awaitable):
            await result
