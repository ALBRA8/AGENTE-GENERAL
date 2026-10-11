"""SQLite-backed MemoryStore.

Uses stdlib `sqlite3` only — no extra deps for the default stack.
Auto-creates the schema. Stores JSON-serialized records.
Composite index on (agent_id, domain, user_id, context_id).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from albra_seed.core.base import MemoryRecord, MemoryStore
from albra_seed.core.types import MemoryType, utc_now


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_records (
    record_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'default',
    user_id TEXT,
    context_id TEXT,
    memory_type TEXT NOT NULL,
    content_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    is_fact INTEGER NOT NULL DEFAULT 0,
    provenance_id TEXT,
    execution_id TEXT,
    correlation_id TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    modified_at TEXT NOT NULL,
    full_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_iso
    ON memory_records(agent_id, domain, user_id, context_id);
CREATE INDEX IF NOT EXISTS idx_memory_type
    ON memory_records(agent_id, domain, memory_type);
CREATE TABLE IF NOT EXISTS memory_tombstones (
    record_id TEXT PRIMARY KEY,
    deleted_at TEXT NOT NULL,
    by TEXT,
    reason TEXT
);
"""


class SQLiteMemoryStore(MemoryStore):
    """SQLite-backed memory store. Strict isolation by composite key."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        # serialize all sqlite access under one lock — sqlite3 in stdlib is
        # not async; a thread lock is sufficient and avoids connection churn.
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        if self._db_path == ":memory:":
            # keep a single shared in-memory connection alive
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _serialize(self, record: MemoryRecord) -> dict[str, Any]:
        return record.model_dump(mode="json")

    async def write(self, record: MemoryRecord) -> str:
        data = self._serialize(record)
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memory_records
                (record_id, agent_id, domain, user_id, context_id, memory_type,
                 content_json, confidence, is_fact, provenance_id, execution_id,
                 correlation_id, version, created_at, modified_at, full_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.record_id,
                    record.agent_id,
                    record.domain,
                    record.user_id,
                    record.context_id,
                    record.memory_type.value,
                    json.dumps(data.get("content")),
                    float(record.confidence),
                    int(bool(record.is_fact)),
                    record.provenance_id,
                    record.execution_id,
                    record.correlation_id,
                    int(record.version),
                    data.get("created_at", utc_now().isoformat()),
                    data.get("modified_at", utc_now().isoformat()),
                    json.dumps(data),
                ),
            )
            self._conn.commit()
        return record.record_id

    def _deserialize(self, row: sqlite3.Row | tuple) -> MemoryRecord:
        full = row[-1] if isinstance(row, tuple) else row["full_json"]
        return MemoryRecord.model_validate_json(full)

    async def read(self, record_id: str) -> MemoryRecord | None:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT * FROM memory_records WHERE record_id = ?",
                (record_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return self._deserialize(row)

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
        sql = (
            "SELECT * FROM memory_records WHERE agent_id = ? AND domain = ? "
            "AND COALESCE(user_id, '') = ? AND COALESCE(context_id, '') = ?"
        )
        params: list[Any] = [
            agent_id,
            domain,
            user_id or "",
            context_id or "",
        ]
        if memory_type is not None:
            sql += " AND memory_type = ?"
            params.append(memory_type.value)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [self._deserialize(r) for r in rows]

    async def delete(
        self,
        record_id: str,
        *,
        by: str | None = None,
        reason: str | None = None,
    ) -> bool:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT 1 FROM memory_records WHERE record_id = ?",
                (record_id,),
            )
            if cur.fetchone() is None:
                return False
            self._conn.execute(
                "DELETE FROM memory_records WHERE record_id = ?", (record_id,)
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_tombstones "
                "(record_id, deleted_at, by, reason) VALUES (?,?,?,?)",
                (record_id, utc_now().isoformat(), by, reason),
            )
            self._conn.commit()
        return True

    async def count(self, *, agent_id: str, domain: str = "default") -> int:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM memory_records WHERE agent_id = ? AND domain = ?",
                (agent_id, domain),
            )
            (n,) = cur.fetchone()
        return int(n)

    async def update(self, record_id: str, **fields: Any) -> MemoryRecord | None:
        record = await self.read(record_id)
        if record is None:
            return None
        for k, v in fields.items():
            if hasattr(record, k) and k != "record_id":
                setattr(record, k, v)
        record.version = (record.version or 1) + 1
        record.modified_at = utc_now()
        await self.write(record)
        return record

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
