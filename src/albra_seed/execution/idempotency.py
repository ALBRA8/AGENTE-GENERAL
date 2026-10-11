"""IdempotencyManager — detects duplicate execution keys.

Callers compute a deterministic key from the task + inputs and register
it with the IdempotencyManager before dispatching an execution. If the
same key has already been registered, the manager returns the original
execution_id so the caller can fetch the cached result instead of
re-running the work.

The manager is intentionally pluggable: in production it can be backed
by Redis or SQLite; the in-memory implementation here is sufficient for
unit tests and CORE_ONLY mode.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from datetime import datetime
from typing import Any

from albra_seed.core.types import utc_now


class IdempotencyRecord:
    """Internal record: maps key -> execution_id with timestamp + payload."""

    __slots__ = ("key", "execution_id", "registered_at", "payload", "status")

    def __init__(
        self,
        *,
        key: str,
        execution_id: str,
        payload: dict[str, Any] | None = None,
        status: str = "pending",
    ) -> None:
        self.key = key
        self.execution_id = execution_id
        self.registered_at = utc_now()
        self.payload: dict[str, Any] = dict(payload or {})
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "execution_id": self.execution_id,
            "registered_at": self.registered_at.isoformat(),
            "status": self.status,
            "payload": dict(self.payload),
        }


class IdempotencyManager:
    """Tracks idempotency keys to detect duplicate executions.

    Supports safe retries: a `register` for a key that already exists
    returns the original record's execution_id rather than failing.
    """

    def __init__(self) -> None:
        self._records: dict[str, IdempotencyRecord] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Key derivation
    # ------------------------------------------------------------------
    @staticmethod
    def derive_key(
        *,
        task: str,
        inputs: dict[str, Any] | None = None,
        agent_id: str | None = None,
        salt: str | None = None,
    ) -> str:
        """Derive a deterministic idempotency key from task + inputs.

        The key is the SHA-256 of a canonical JSON of {task, inputs,
        agent_id, salt}. Stable across processes; safe to persist.
        """
        canonical = json.dumps(
            {
                "task": task,
                "inputs": inputs or {},
                "agent_id": agent_id or "",
                "salt": salt or "",
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return "idem_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # Register / lookup / query
    # ------------------------------------------------------------------
    def register(
        self,
        key: str,
        execution_id: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> IdempotencyRecord:
        """Register a key. If the key already exists, the original record
        is returned untouched (safe retry)."""
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return existing
            rec = IdempotencyRecord(
                key=key,
                execution_id=execution_id,
                payload=payload,
            )
            self._records[key] = rec
            return rec

    def lookup(self, key: str) -> IdempotencyRecord | None:
        with self._lock:
            return self._records.get(key)

    def is_duplicate(self, key: str) -> bool:
        with self._lock:
            return key in self._records

    def update_status(self, key: str, status: str) -> bool:
        with self._lock:
            rec = self._records.get(key)
            if rec is None:
                return False
            rec.status = status
            return True

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    # ------------------------------------------------------------------
    # Async wrappers (so callers can await without juggling threads)
    # ------------------------------------------------------------------
    async def aregister(self, key: str, execution_id: str, **kw: Any) -> IdempotencyRecord:
        return await asyncio.to_thread(self.register, key, execution_id, **kw)

    async def alookup(self, key: str) -> IdempotencyRecord | None:
        return await asyncio.to_thread(self.lookup, key)

    async def ais_duplicate(self, key: str) -> bool:
        return await asyncio.to_thread(self.is_duplicate, key)


__all__ = ["IdempotencyManager", "IdempotencyRecord"]
