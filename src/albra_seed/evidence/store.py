"""Evidence store — abstract + SQLite + in-memory.

No external dependencies beyond stdlib `sqlite3`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from albra_seed.core.types import Evidence, utc_now


_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_records (
    evidence_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    content_json TEXT,
    content_ref TEXT,
    evidence_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    provenance_json TEXT NOT NULL DEFAULT '[]',
    execution_id TEXT,
    integrity_hash TEXT,
    full_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ev_source ON evidence_records(source_type);
CREATE INDEX IF NOT EXISTS idx_ev_type ON evidence_records(evidence_type);
CREATE INDEX IF NOT EXISTS idx_ev_exec ON evidence_records(execution_id);
"""


class EvidenceStore(ABC):
    @abstractmethod
    async def register(self, evidence: Evidence) -> str:
        raise NotImplementedError

    @abstractmethod
    async def get(self, evidence_id: str) -> Evidence | None:
        raise NotImplementedError

    @abstractmethod
    async def list_all(self, *, limit: int = 100) -> list[Evidence]:
        raise NotImplementedError

    @abstractmethod
    async def find_by_execution(self, execution_id: str) -> list[Evidence]:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, evidence_id: str) -> bool:
        raise NotImplementedError


class InMemoryEvidenceStore(EvidenceStore):
    def __init__(self) -> None:
        self._records: dict[str, Evidence] = {}

    async def register(self, evidence: Evidence) -> str:
        self._records[evidence.evidence_id] = evidence
        return evidence.evidence_id

    async def get(self, evidence_id: str) -> Evidence | None:
        return self._records.get(evidence_id)

    async def list_all(self, *, limit: int = 100) -> list[Evidence]:
        return list(self._records.values())[:limit]

    async def find_by_execution(self, execution_id: str) -> list[Evidence]:
        return [
            e for e in self._records.values() if e.execution_id == execution_id
        ]

    async def delete(self, evidence_id: str) -> bool:
        return self._records.pop(evidence_id, None) is not None


class SQLiteEvidenceStore(EvidenceStore):
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

    async def register(self, evidence: Evidence) -> str:
        data = evidence.model_dump(mode="json")
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO evidence_records
                (evidence_id, source, source_type, timestamp, content_json,
                 content_ref, evidence_type, confidence, provenance_json,
                 execution_id, integrity_hash, full_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    evidence.evidence_id,
                    evidence.source,
                    evidence.source_type,
                    str(evidence.timestamp),
                    json.dumps(data.get("content")),
                    evidence.content_ref,
                    evidence.evidence_type.value,
                    float(evidence.confidence),
                    json.dumps(list(evidence.provenance or [])),
                    evidence.execution_id,
                    evidence.integrity_hash,
                    json.dumps(data),
                ),
            )
            self._conn.commit()
        return evidence.evidence_id

    def _row(self, row: sqlite3.Row | tuple) -> Evidence:
        full = row[-1] if isinstance(row, tuple) else row["full_json"]
        return Evidence.model_validate_json(full)

    async def get(self, evidence_id: str) -> Evidence | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM evidence_records WHERE evidence_id = ?",
                (evidence_id,),
            )
            row = cur.fetchone()
        return self._row(row) if row else None

    async def list_all(self, *, limit: int = 100) -> list[Evidence]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM evidence_records ORDER BY timestamp DESC LIMIT ?",
                (int(limit),),
            )
            rows = cur.fetchall()
        return [self._row(r) for r in rows]

    async def find_by_execution(self, execution_id: str) -> list[Evidence]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM evidence_records WHERE execution_id = ?",
                (execution_id,),
            )
            rows = cur.fetchall()
        return [self._row(r) for r in rows]

    async def delete(self, evidence_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM evidence_records WHERE evidence_id = ?",
                (evidence_id,),
            )
            if cur.fetchone() is None:
                return False
            self._conn.execute(
                "DELETE FROM evidence_records WHERE evidence_id = ?",
                (evidence_id,),
            )
            self._conn.commit()
        return True

    def close(self) -> None:
        with self._lock:
            self._conn.close()
