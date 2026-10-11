"""SQLiteJobQueue — SQLite-backed durable JobQueue (stdlib sqlite3 only).

Two tables:
  - jobs               — full job state (one row per job, JSON-serialized)
  - job_queue_order    — FIFO ordering for dequeue (one row per enqueue)

`dequeue()` removes the order entry but leaves the `jobs` row intact
(the manager updates status subsequently). `count_pending()` joins both
tables so jobs already dequeued are not counted.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from albra_seed.jobs.job import Job, JobStatus
from albra_seed.jobs.queue import JobQueue


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    retries INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    last_attempt_at TEXT,
    result_json TEXT,
    error TEXT,
    execution_id TEXT,
    metadata_json TEXT,
    full_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_pending ON jobs(status, created_at);
CREATE TABLE IF NOT EXISTS job_queue_order (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL
);
"""


class SQLiteJobQueue(JobQueue):
    """SQLite-backed job queue. Uses stdlib `sqlite3` only."""

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
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    def enqueue(self, job: Job) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "INSERT OR REPLACE INTO jobs (id, type, payload_json, status, retries, "
                "max_retries, created_at, started_at, finished_at, last_attempt_at, "
                "result_json, error, execution_id, metadata_json, full_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._row_from_job(job),
            )
            self._conn.execute(
                "INSERT INTO job_queue_order (job_id) VALUES (?)", (job.id,)
            )
            self._conn.commit()

    def dequeue(self) -> Job | None:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                """
                SELECT j.full_json FROM jobs j
                JOIN job_queue_order q ON q.job_id = j.id
                WHERE j.status = ?
                ORDER BY q.seq ASC LIMIT 1
                """,
                (JobStatus.PENDING.value,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            job = Job.model_validate_json(row[0])
            self._conn.execute(
                "DELETE FROM job_queue_order WHERE job_id = ?", (job.id,)
            )
            self._conn.commit()
            return job

    def peek(self) -> Job | None:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                """
                SELECT j.full_json FROM jobs j
                JOIN job_queue_order q ON q.job_id = j.id
                WHERE j.status = ?
                ORDER BY q.seq ASC LIMIT 1
                """,
                (JobStatus.PENDING.value,),
            )
            row = cur.fetchone()
            return Job.model_validate_json(row[0]) if row else None

    def update(self, job: Job) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "INSERT OR REPLACE INTO jobs (id, type, payload_json, status, retries, "
                "max_retries, created_at, started_at, finished_at, last_attempt_at, "
                "result_json, error, execution_id, metadata_json, full_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._row_from_job(job),
            )
            self._conn.commit()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT full_json FROM jobs WHERE id = ?", (job_id,)
            )
            row = cur.fetchone()
            return Job.model_validate_json(row[0]) if row else None

    def list(self, status: JobStatus | None = None) -> list[Job]:
        with self._lock:
            assert self._conn is not None
            if status is None:
                cur = self._conn.execute(
                    "SELECT full_json FROM jobs ORDER BY created_at ASC"
                )
            else:
                cur = self._conn.execute(
                    "SELECT full_json FROM jobs WHERE status = ? ORDER BY created_at ASC",
                    (status.value,),
                )
            return [Job.model_validate_json(r[0]) for r in cur.fetchall()]

    def remove(self, job_id: str) -> bool:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,))
            existed = cur.fetchone() is not None
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._conn.execute(
                "DELETE FROM job_queue_order WHERE job_id = ?", (job_id,)
            )
            self._conn.commit()
            return existed

    def count_pending(self) -> int:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                """
                SELECT COUNT(*) FROM job_queue_order q
                JOIN jobs j ON q.job_id = j.id
                WHERE j.status = ?
                """,
                (JobStatus.PENDING.value,),
            )
            (n,) = cur.fetchone()
            return int(n)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    def _row_from_job(self, job: Job) -> tuple[Any, ...]:
        full = job.model_dump(mode="json")
        return (
            job.id,
            job.type,
            json.dumps(job.payload),
            job.status.value,
            int(job.retries),
            int(job.max_retries),
            job.created_at.isoformat(),
            job.started_at.isoformat() if job.started_at else None,
            job.finished_at.isoformat() if job.finished_at else None,
            job.last_attempt_at.isoformat() if job.last_attempt_at else None,
            json.dumps(job.result) if job.result is not None else None,
            job.error,
            job.execution_id,
            json.dumps(job.metadata),
            json.dumps(full),
        )


__all__ = ["SQLiteJobQueue"]
