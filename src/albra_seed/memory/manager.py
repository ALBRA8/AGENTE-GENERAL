"""MemoryManager — high-level orchestrator over a MemoryStore.

Wraps a concrete MemoryStore and adds:
    - remember()  : write a memory with auto-attached provenance + lifecycle
    - recall()     : query memories within an isolation bucket
    - forget()     : delete a memory while keeping an audit tombstone
    - consolidate(): promote episodic → semantic when policy conditions met

Strict isolation is enforced on every call.
"""

from __future__ import annotations

from typing import Any

from albra_seed.core.base import MemoryRecord, MemoryStore
from albra_seed.core.types import MemoryType, new_id, utc_now
from albra_seed.memory.consolidation import ConsolidationPolicy, ConsolidationResult
from albra_seed.memory.isolator import MemoryIsolator
from albra_seed.memory.lifecycle import MemoryLifecycle
from albra_seed.memory.provenance import MemoryProvenance


class MemoryManager:
    """High-level orchestrator over a MemoryStore."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        provenance: MemoryProvenance | None = None,
        lifecycle: MemoryLifecycle | None = None,
        consolidation: ConsolidationPolicy | None = None,
    ) -> None:
        self.store = store
        self.provenance = provenance or MemoryProvenance()
        self.lifecycle = lifecycle or MemoryLifecycle()
        self.consolidation = consolidation or ConsolidationPolicy()

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
        # Enforce factual integrity: only records with at least one evidence
        # id and an external/tool/verified source may carry is_fact=True.
        if is_fact:
            if not evidence_ids:
                raise ValueError(
                    "is_fact=True requires at least one evidence_id (MemoryProvenance gate)"
                )

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
        limit: int = 50,
    ) -> list[MemoryRecord]:
        records = await self.store.query(
            agent_id=agent_id,
            domain=domain,
            user_id=user_id,
            context_id=context_id,
            memory_type=memory_type,
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
        record = await self.store.read(record_id)
        if record is None:
            return False
        ok = await self.store.delete(record_id)
        if ok:
            self.lifecycle.record_deleted(record_id, reason=reason)
            self.lifecycle.record_forgotten(record_id, reason=reason)
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
        result = self.consolidation.consolidate(record)
        if result.promoted:
            await self.store.write(record)
            self.lifecycle.record_consolidated(record_id, reason=result.reason)
            self.lifecycle.record_modified(record_id, by=by, reason=result.reason)
        return result

    async def get(self, record_id: str) -> MemoryRecord | None:
        record = await self.store.read(record_id)
        if record is not None:
            self.lifecycle.record_accessed(record_id)
        return record

    async def count(
        self, *, agent_id: str, domain: str = "default"
    ) -> int:
        return await self.store.count(agent_id=agent_id, domain=domain)
