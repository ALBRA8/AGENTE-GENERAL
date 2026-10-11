"""JobQueue interface + InMemoryJobQueue.

The interface is intentionally minimal:
  - enqueue(job)         -> adds to the pending set
  - dequeue()            -> pops the next pending job (FIFO)
  - peek()               -> look at next pending without removing
  - update(job)          -> upserts an existing job by id
  - get(id)              -> fetch by id
  - list(status=None)    -> list jobs (optionally filtered by status)
  - remove(id)           -> delete by id
  - count_pending()      -> # pending still in queue
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import deque

from albra_seed.jobs.job import Job, JobStatus


class JobQueue(ABC):
    """Abstract JobQueue interface."""

    @abstractmethod
    def enqueue(self, job: Job) -> None: ...

    @abstractmethod
    def dequeue(self) -> Job | None: ...

    @abstractmethod
    def peek(self) -> Job | None: ...

    @abstractmethod
    def update(self, job: Job) -> None: ...

    @abstractmethod
    def get(self, job_id: str) -> Job | None: ...

    @abstractmethod
    def list(self, status: JobStatus | None = None) -> list[Job]: ...

    @abstractmethod
    def remove(self, job_id: str) -> bool: ...

    @abstractmethod
    def count_pending(self) -> int: ...


# =====================================================================
# InMemoryJobQueue
# =====================================================================


class InMemoryJobQueue(JobQueue):
    """In-memory FIFO queue + lookup index. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: deque[str] = deque()
        self._jobs: dict[str, Job] = {}

    def enqueue(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            self._pending.append(job.id)

    def dequeue(self) -> Job | None:
        with self._lock:
            while self._pending:
                jid = self._pending.popleft()
                job = self._jobs.get(jid)
                if job is None or job.status != JobStatus.PENDING:
                    continue
                return job
            return None

    def peek(self) -> Job | None:
        with self._lock:
            for jid in list(self._pending):
                job = self._jobs.get(jid)
                if job and job.status == JobStatus.PENDING:
                    return job
            return None

    def update(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, status: JobStatus | None = None) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        return jobs

    def remove(self, job_id: str) -> bool:
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
            if existed:
                try:
                    self._pending.remove(job_id)
                except ValueError:
                    pass
            return existed

    def count_pending(self) -> int:
        # Count jobs that are still in the pending queue (not yet
        # dequeued) and whose status is still PENDING.
        with self._lock:
            return sum(
                1
                for jid in self._pending
                if (job := self._jobs.get(jid)) is not None
                and job.status == JobStatus.PENDING
            )


__all__ = ["JobQueue", "InMemoryJobQueue"]
