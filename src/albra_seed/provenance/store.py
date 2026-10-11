"""Provenance store — abstract + SQLite + in-memory.

No external dependencies beyond stdlib `sqlite3`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from albra_seed.core.types import Provenance, utc_now


_SCHEMA = """
CREATE TABLE IF NOT EXISTS provenance_records (
    provenance_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    parent_provenance_id TEXT,
    execution_id TEXT,
    correlation_id TEXT,
    modified_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    record_kind TEXT NOT NULL DEFAULT 'unknown',
    record_ref TEXT,
    full_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prov_parent
    ON provenance_records(parent_provenance_id);
CREATE INDEX IF NOT EXISTS idx_prov_corr
    ON provenance_records(correlation_id);
CREATE INDEX IF NOT EXISTS idx_prov_exec
    ON provenance_records(execution_id);
CREATE INDEX IF NOT EXISTS idx_prov_ref
    ON provenance_records(record_kind, record_ref);
"""


class ProvenanceStore(ABC):
    """Abstract provenance store. record_kind: input|output|memory|evidence|
    tool_call|provider_call|decision|workflow. record_ref: the id of the
    referenced record (memory record id, evidence id, tool call id, ...).
    """

    @abstractmethod
    async def record(
        self,
        provenance: Provenance,
        *,
        record_kind: str = "unknown",
        record_ref: str | None = None,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    async def get(self, provenance_id: str) -> Provenance | None:
        raise NotImplementedError

    @abstractmethod
    async def find_by_ref(
        self, record_kind: str, record_ref: str
    ) -> list[Provenance]:
        raise NotImplementedError

    @abstractmethod
    async def find_by_execution(self, execution_id: str) -> list[Provenance]:
        raise NotImplementedError

    @abstractmethod
    async def find_by_correlation(self, correlation_id: str) -> list[Provenance]:
        raise NotImplementedError

    @abstractmethod
    async def children(self, parent_provenance_id: str) -> list[Provenance]:
        raise NotImplementedError


class InMemoryProvenanceStore(ProvenanceStore):
    def __init__(self) -> None:
        self._by_id: dict[str, tuple[Provenance, str, str | None]] = {}

    async def record(
        self,
        provenance: Provenance,
        *,
        record_kind: str = "unknown",
        record_ref: str | None = None,
    ) -> str:
        self._by_id[provenance.provenance_id] = (provenance, record_kind, record_ref)
        return provenance.provenance_id

    async def get(self, provenance_id: str) -> Provenance | None:
        rec = self._by_id.get(provenance_id)
        return rec[0] if rec else None

    async def find_by_ref(
        self, record_kind: str, record_ref: str
    ) -> list[Provenance]:
        return [
            p
            for (p, k, r) in self._by_id.values()
            if k == record_kind and r == record_ref
        ]

    async def find_by_execution(self, execution_id: str) -> list[Provenance]:
        return [p for (p, _, _) in self._by_id.values() if p.execution_id == execution_id]

    async def find_by_correlation(self, correlation_id: str) -> list[Provenance]:
        return [
            p
            for (p, _, _) in self._by_id.values()
            if p.correlation_id == correlation_id
        ]

    async def children(self, parent_provenance_id: str) -> list[Provenance]:
        return [
            p
            for (p, _, _) in self._by_id.values()
            if p.parent_provenance_id == parent_provenance_id
        ]


class SQLiteProvenanceStore(ProvenanceStore):
    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        if self._db_path == ":memory:":
            self._conn: sqlite3.Connection = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
        else:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    async def record(
        self,
        provenance: Provenance,
        *,
        record_kind: str = "unknown",
        record_ref: str | None = None,
    ) -> str:
        data = provenance.model_dump(mode="json")
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO provenance_records
                (provenance_id, source, source_type, timestamp, confidence,
                 evidence_ids_json, parent_provenance_id, execution_id,
                 correlation_id, modified_at, version, record_kind, record_ref,
                 full_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    provenance.provenance_id,
                    provenance.source,
                    provenance.source_type,
                    str(provenance.timestamp),
                    float(provenance.confidence),
                    json.dumps(list(provenance.evidence_ids or [])),
                    provenance.parent_provenance_id,
                    provenance.execution_id,
                    provenance.correlation_id,
                    str(provenance.modified_at or utc_now()),
                    int(provenance.version),
                    record_kind,
                    record_ref,
                    json.dumps(data),
                ),
            )
            self._conn.commit()
        return provenance.provenance_id

    def _row(self, row: Any) -> Provenance:
        if isinstance(row, sqlite3.Row):
            full = row["full_json"]
        else:
            full = row[-1]
        return Provenance.model_validate_json(full)

    async def get(self, provenance_id: str) -> Provenance | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM provenance_records WHERE provenance_id = ?",
                (provenance_id,),
            )
            row = cur.fetchone()
        return self._row(row) if row else None

    async def find_by_ref(
        self, record_kind: str, record_ref: str
    ) -> list[Provenance]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM provenance_records WHERE record_kind = ? AND record_ref = ?",
                (record_kind, record_ref),
            )
            rows = cur.fetchall()
        return [self._row(r) for r in rows]

    async def find_by_execution(self, execution_id: str) -> list[Provenance]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM provenance_records WHERE execution_id = ?",
                (execution_id,),
            )
            rows = cur.fetchall()
        return [self._row(r) for r in rows]

    async def find_by_correlation(self, correlation_id: str) -> list[Provenance]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM provenance_records WHERE correlation_id = ?",
                (correlation_id,),
            )
            rows = cur.fetchall()
        return [self._row(r) for r in rows]

    async def children(self, parent_provenance_id: str) -> list[Provenance]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM provenance_records WHERE parent_provenance_id = ?",
                (parent_provenance_id,),
            )
            rows = cur.fetchall()
        return [self._row(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
