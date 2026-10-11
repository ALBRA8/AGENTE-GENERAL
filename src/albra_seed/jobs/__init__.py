"""Jobs subpackage — JobManager + Job + JobQueue + JobHistory.

Public API:
    JobManager       — submit, execute, retry, cancel, persist
    Job              — pydantic model of a single job
    JobStatus        — PENDING / RUNNING / SUCCEEDED / FAILED / CANCELLED / TIMEOUT
    JobQueue         — abstract queue interface
    InMemoryJobQueue — in-memory FIFO queue
    SQLiteJobQueue   — SQLite-backed durable queue (stdlib sqlite3 only)
    JobHistory       — abstract history backend
    InMemoryJobHistory — in-memory history
    SQLiteJobHistory   — SQLite-backed durable history
"""

from albra_seed.jobs.manager import JobHandler, JobManager
from albra_seed.jobs.job import Job, JobStatus
from albra_seed.jobs.queue import InMemoryJobQueue, JobQueue
from albra_seed.jobs.sqlite_queue import SQLiteJobQueue
from albra_seed.jobs.history import (
    InMemoryJobHistory,
    JobHistory,
    SQLiteJobHistory,
)

__all__ = [
    "JobManager",
    "Job",
    "JobStatus",
    "JobQueue",
    "InMemoryJobQueue",
    "SQLiteJobQueue",
    "JobHistory",
    "InMemoryJobHistory",
    "SQLiteJobHistory",
    "JobHandler",
]
