"""In-memory MemoryStore — dict-backed.

For CORE_ONLY mode and tests. No external dependencies.
"""

from __future__ import annotations

import asyncio
from typing import Any

from albra_seed.core.base import MemoryRecord, MemoryStore
from albra_seed.core.types import MemoryType, new_id, utc_now
from albra_seed.memory.isolator import MemoryIsolator


class InMemoryMemoryStore(MemoryStore):
    """Dict-backed MemoryStore. Strictly isolates by composite key."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._lock = asyncio.Lock()
        # audit tombstones — record_id -> {deleted_at, by, reason}
        self._tombstones: dict[str, dict[str, Any]] = {}

    async def write(self, record: MemoryRecord) -> str:
        async with self._lock:
            if not record.record_id:
                record.record_id = new_id("mem")
            self._records[record.record_id] = record
            return record.record_id

    async def read(self, record_id: str) -> MemoryRecord | None:
        return self._records.get(record_id)

    async def query(
        self,
        *,
        agent_id: str,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        memory_type: MemoryType | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        want = MemoryIsolator.key_for(agent_id, domain, user_id, context_id)
        results: list[MemoryRecord] = []
        for record in self._records.values():
            have = MemoryIsolator.key_for(
                record.agent_id,
                record.domain,
                record.user_id,
                record.context_id,
            )
            if have != want:
                continue
            if memory_type is not None and record.memory_type != memory_type:
                continue
            results.append(record)
            if len(results) >= limit:
                break
        return results

    async def delete(
        self,
        record_id: str,
        *,
        by: str | None = None,
        reason: str | None = None,
    ) -> bool:
        async with self._lock:
            if record_id not in self._records:
                return False
            del self._records[record_id]
            self._tombstones[record_id] = {
                "deleted_at": utc_now().isoformat(),
                "by": by,
                "reason": reason,
            }
            return True

    async def count(self, *, agent_id: str, domain: str = "default") -> int:
        n = 0
        for record in self._records.values():
            if record.agent_id == agent_id and record.domain == domain:
                n += 1
        return n

    async def update(self, record_id: str, **fields: Any) -> MemoryRecord | None:
        async with self._lock:
            record = self._records.get(record_id)
            if record is None:
                return None
            for k, v in fields.items():
                if hasattr(record, k):
                    setattr(record, k, v)
            record.version = (record.version or 1) + 1
            self._records[record_id] = record
            return record
