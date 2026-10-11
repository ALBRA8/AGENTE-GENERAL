"""JobHistory — durable store of completed jobs for audit + inspection.

History entries are terminal-state Jobs (SUCCEEDED / FAILED / CANCELLED /
TIMEOUT). The history is append-only: callers may inspect past jobs but
cannot mutate them through this interface.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from albra_seed.jobs.job import Job, JobStatus


_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS job_history (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    retries INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    result_json TEXT,
    full_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_status ON job_history(status);
CREATE INDEX IF NOT EXISTS idx_history_type ON job_history(type);
"""


class JobHistory(ABC):
    """Abstract history backend."""

    @abstractmethod
    def record(self, job: Job) -> None: ...

    @abstractmethod
    def get(self, job_id: str) -> Job | None: ...

    @abstractmethod
    def list(self, *, status: JobStatus | None = None, limit: int = 100) -> list[Job]: ...

    @abstractmethod
    def count(self, *, status: JobStatus | None = None) -> int: ...

    @abstractmethod
    def clear(self) -> None: ...


# =====================================================================
# In-memory history
# =====================================================================


class InMemoryJobHistory(JobHistory):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, Job] = {}

    def record(self, job: Job) -> None:
        with self._lock:
            self._records[job.id] = job.model_copy(deep=True)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._records.get(job_id)
            return job.model_copy(deep=True) if job else None

    def list(self, *, status: JobStatus | None = None, limit: int = 100) -> list[Job]:
        with self._lock:
            records = list(self._records.values())
        if status is not None:
            records = [r for r in records if r.status == status]
        records = sorted(records, key=lambda r: r.created_at, reverse=True)
        return records[:limit]

    def count(self, *, status: JobStatus | None = None) -> int:
        with self._lock:
            if status is None:
                return len(self._records)
            return sum(1 for r in self._records.values() if r.status == status)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


# =====================================================================
# SQLite history
# =====================================================================


class SQLiteJobHistory(JobHistory):
    """SQLite-backed job history. Uses stdlib `sqlite3`."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        if self._db_path == ":memory:":
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.executescript(_HISTORY_SCHEMA)
        self._conn.commit()

    def record(self, job: Job) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "INSERT OR REPLACE INTO job_history "
                "(id, type, status, retries, created_at, finished_at, error, result_json, full_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job.id,
                    job.type,
                    job.status.value,
                    int(job.retries),
                    job.created_at.isoformat(),
                    job.finished_at.isoformat() if job.finished_at else None,
                    job.error,
                    json.dumps(job.result) if job.result is not None else None,
                    json.dumps(job.model_dump(mode="json")),
                ),
            )
            self._conn.commit()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT full_json FROM job_history WHERE id = ?", (job_id,)
            )
            row = cur.fetchone()
            return Job.model_validate_json(row[0]) if row else None

    def list(self, *, status: JobStatus | None = None, limit: int = 100) -> list[Job]:
        with self._lock:
            assert self._conn is not None
            if status is None:
                cur = self._conn.execute(
                    "SELECT full_json FROM job_history ORDER BY created_at DESC LIMIT ?",
                    (int(limit),),
                )
            else:
                cur = self._conn.execute(
                    "SELECT full_json FROM job_history WHERE status = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status.value, int(limit)),
                )
            return [Job.model_validate_json(r[0]) for r in cur.fetchall()]

    def count(self, *, status: JobStatus | None = None) -> int:
        with self._lock:
            assert self._conn is not None
            if status is None:
                cur = self._conn.execute("SELECT COUNT(*) FROM job_history")
            else:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM job_history WHERE status = ?",
                    (status.value,),
                )
            (n,) = cur.fetchone()
            return int(n)

    def clear(self) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute("DELETE FROM job_history")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


__all__ = ["JobHistory", "InMemoryJobHistory", "SQLiteJobHistory"]
