"""Feedback store — abstract + InMemory + SQLite.

Same pattern as `evidence/store.py` and `provenance/store.py`:
  * `FeedbackStore`           — ABC.
  * `InMemoryFeedbackStore`   — dict-backed (default, tests, dev).
  * `SQLiteFeedbackStore`     — sqlite3-backed (stdlib only).

Methods (mirrors EvidenceStore shape):
  * `write(record)            -> feedback_id`
  * `read(feedback_id)        -> FeedbackRecord | None`
  * `query(*, agent_id, ...)   -> list[FeedbackRecord]`
  * `count()                   -> int`

Stores are append-mostly: `write` upserts on `feedback_id` so a caller
can correct a record's metadata in place. The actual evaluation text /
type / outcome fields are NOT mutated after first write in normal use
(link_to_decision in FeedbackManager re-writes with an updated
decision_id — the canonical use case for upsert).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from albra_seed.feedback.types import FeedbackRecord, FeedbackType

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback_records (
    feedback_id    TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL,
    execution_id   TEXT,
    correlation_id TEXT,
    task_id        TEXT,
    context_id     TEXT,
    feedback_type  TEXT NOT NULL,
    source         TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    evaluation     TEXT NOT NULL,
    confidence     REAL NOT NULL DEFAULT 0.0,
    evidence_ids   TEXT NOT NULL DEFAULT '[]',
    provenance_id  TEXT,
    decision_id   TEXT,
    skill_id      TEXT,
    expected_json  TEXT,
    actual_json    TEXT,
    timestamp      TEXT NOT NULL,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    full_json      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fb_agent  ON feedback_records(agent_id);
CREATE INDEX IF NOT EXISTS idx_fb_exec   ON feedback_records(execution_id);
CREATE INDEX IF NOT EXISTS idx_fb_type   ON feedback_records(feedback_type);
CREATE INDEX IF NOT EXISTS idx_fb_corr   ON feedback_records(correlation_id);
"""


class FeedbackStore(ABC):
    """Abstract persistence backend for FeedbackRecord."""

    @abstractmethod
    async def write(self, record: FeedbackRecord) -> str:
        raise NotImplementedError

    @abstractmethod
    async def read(self, feedback_id: str) -> FeedbackRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def query(
        self,
        *,
        agent_id: str | None = None,
        execution_id: str | None = None,
        feedback_type: FeedbackType | None = None,
        limit: int = 50,
    ) -> list[FeedbackRecord]:
        raise NotImplementedError

    @abstractmethod
    async def count(self) -> int:
        raise NotImplementedError


class InMemoryFeedbackStore(FeedbackStore):
    """Dict-backed store — thread-safe via a single lock."""

    def __init__(self) -> None:
        self._records: dict[str, FeedbackRecord] = {}
        self._lock = threading.Lock()

    async def write(self, record: FeedbackRecord) -> str:
        with self._lock:
            self._records[record.feedback_id] = record
        return record.feedback_id

    async def read(self, feedback_id: str) -> FeedbackRecord | None:
        with self._lock:
            return self._records.get(feedback_id)

    async def query(
        self,
        *,
        agent_id: str | None = None,
        execution_id: str | None = None,
        feedback_type: FeedbackType | None = None,
        limit: int = 50,
    ) -> list[FeedbackRecord]:
        with self._lock:
            out: list[FeedbackRecord] = []
            for rec in self._records.values():
                if agent_id is not None and rec.agent_id != agent_id:
                    continue
                if execution_id is not None and rec.execution_id != execution_id:
                    continue
                if feedback_type is not None and rec.feedback_type != feedback_type:
                    continue
                out.append(rec)
            out.sort(key=lambda r: r.timestamp, reverse=True)
            return out[: max(0, int(limit))]

    async def count(self) -> int:
        with self._lock:
            return len(self._records)


class SQLiteFeedbackStore(FeedbackStore):
    """sqlite3-backed store (stdlib only). Same row pattern as the
    other SQLite stores in the SEED: store the canonical JSON of the
    full pydantic model in `full_json`, plus indexed columns for the
    fields most commonly filtered on.
    """

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

    async def write(self, record: FeedbackRecord) -> str:
        data = record.model_dump(mode="json")
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO feedback_records
                (feedback_id, agent_id, execution_id, correlation_id,
                 task_id, context_id, feedback_type, source, source_type,
                 evaluation, confidence, evidence_ids, provenance_id,
                 decision_id, skill_id, expected_json, actual_json,
                 timestamp, metadata_json, full_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.feedback_id,
                    record.agent_id,
                    record.execution_id,
                    record.correlation_id,
                    record.task_id,
                    record.context_id,
                    record.feedback_type.value,
                    record.source,
                    record.source_type,
                    record.evaluation,
                    float(record.confidence),
                    json.dumps(list(record.evidence_ids or [])),
                    record.provenance_id,
                    record.decision_id,
                    record.skill_id,
                    json.dumps(data.get("expected_outcome"), default=str),
                    json.dumps(data.get("actual_outcome"), default=str),
                    str(record.timestamp),
                    json.dumps(record.metadata, default=str),
                    json.dumps(data),
                ),
            )
            self._conn.commit()
        return record.feedback_id

    def _row(self, row: sqlite3.Row | tuple) -> FeedbackRecord:
        full = row["full_json"] if isinstance(row, sqlite3.Row) else row[-1]
        return FeedbackRecord.model_validate_json(full)

    async def read(self, feedback_id: str) -> FeedbackRecord | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM feedback_records WHERE feedback_id = ?",
                (feedback_id,),
            )
            row = cur.fetchone()
        return self._row(row) if row else None

    async def query(
        self,
        *,
        agent_id: str | None = None,
        execution_id: str | None = None,
        feedback_type: FeedbackType | None = None,
        limit: int = 50,
    ) -> list[FeedbackRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if execution_id is not None:
            clauses.append("execution_id = ?")
            params.append(execution_id)
        if feedback_type is not None:
            clauses.append("feedback_type = ?")
            params.append(feedback_type.value)
        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(max(0, int(limit)))
        with self._lock:
            cur = self._conn.execute(
                f"SELECT * FROM feedback_records WHERE {where} "
                "ORDER BY timestamp DESC LIMIT ?",
                params,
            )
            rows = cur.fetchall()
        return [self._row(r) for r in rows]

    async def count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM feedback_records")
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "FeedbackStore",
    "InMemoryFeedbackStore",
    "SQLiteFeedbackStore",
]
