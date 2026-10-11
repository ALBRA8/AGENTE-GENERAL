"""JobManager — queue, execute, retry, cancel, persist jobs.

The manager wraps a JobQueue + a JobHistory. Execution is delegated to
caller-supplied async handlers, keyed by `job.type`. Retries honor
`max_retries` with exponential backoff. Cancellation flips status to
CANCELLED and the in-flight handler observes `asyncio.CancelledError`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from albra_seed.jobs.history import JobHistory
from albra_seed.jobs.job import Job, JobStatus
from albra_seed.jobs.queue import InMemoryJobQueue, JobQueue


JobHandler = Callable[[Job], Awaitable[Any]]
CancelPredicate = Callable[[Job], bool]


class JobManager:
    """Submit, execute, retry, cancel, and persist Jobs."""

    def __init__(
        self,
        *,
        queue: JobQueue | None = None,
        history: JobHistory | None = None,
        handlers: dict[str, JobHandler] | None = None,
        default_timeout_seconds: float = 60.0,
        base_retry_delay_seconds: float = 0.05,
        max_retry_delay_seconds: float = 2.0,
    ) -> None:
        self.queue: JobQueue = queue or InMemoryJobQueue()
        self.history: JobHistory = history if history is not None else _default_history()
        self._handlers: dict[str, JobHandler] = dict(handlers or {})
        self.default_timeout_seconds = default_timeout_seconds
        self.base_retry_delay_seconds = base_retry_delay_seconds
        self.max_retry_delay_seconds = max_retry_delay_seconds
        self._cancelled: set[str] = set()
        self._inflight: dict[str, asyncio.Task[Any]] = {}

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------
    def register_handler(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    # ------------------------------------------------------------------
    # Submit + status
    # ------------------------------------------------------------------
    def submit(
        self,
        *,
        type: str,
        payload: dict[str, Any] | None = None,
        max_retries: int = 0,
        execution_id: str | None = None,
    ) -> Job:
        job = Job(
            type=type,
            payload=dict(payload or {}),
            max_retries=max_retries,
            execution_id=execution_id,
        )
        self.queue.enqueue(job)
        return job

    def status(self, job_id: str) -> JobStatus | None:
        job = self.queue.get(job_id)
        if job is None:
            job = self.history.get(job_id)
        return job.status if job is not None else None

    def get(self, job_id: str) -> Job | None:
        job = self.queue.get(job_id)
        if job is None:
            job = self.history.get(job_id)
        return job

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    async def run_one(self) -> Job | None:
        """Dequeue the next pending job and execute it. Returns the Job
        (terminal state) or None if the queue is empty."""
        job = self.queue.dequeue()
        if job is None:
            return None
        # Track the running asyncio task so cancel() can interrupt it.
        current = asyncio.current_task()
        if current is not None:
            self._inflight[job.id] = current
        try:
            await self._execute_with_retries(job)
        finally:
            self._inflight.pop(job.id, None)
        return job

    async def run_until_empty(self, *, max_iterations: int = 1000) -> int:
        """Drain the queue. Returns number of jobs executed."""
        n = 0
        for _ in range(max_iterations):
            job = await self.run_one()
            if job is None:
                break
            n += 1
        return n

    async def _execute_with_retries(self, job: Job) -> Job:
        handler = self._handlers.get(job.type)
        if handler is None:
            job.mark_failed(f"no handler registered for job type '{job.type}'")
            self.queue.update(job)
            self.history.record(job)
            return job

        attempts = job.max_retries + 1
        while True:
            if job.id in self._cancelled:
                job.mark_cancelled()
                self.queue.update(job)
                self.history.record(job)
                return job
            job.mark_running()
            self.queue.update(job)
            try:
                result = await asyncio.wait_for(
                    handler(job),
                    timeout=self.default_timeout_seconds,
                )
                job.mark_succeeded(result=result)
                self.queue.update(job)
                self.history.record(job)
                return job
            except asyncio.CancelledError:
                job.mark_cancelled()
                self.queue.update(job)
                self.history.record(job)
                raise
            except asyncio.TimeoutError:
                job.mark_timeout()
            except Exception as exc:  # noqa: BLE001
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = JobStatus.FAILED
                job.finished_at = None  # not terminal yet; may retry

            attempts_left = attempts - (job.retries + 1)
            if attempts_left <= 0:
                # Final failure — record terminal.
                if job.status == JobStatus.FAILED:
                    job.finished_at = job.finished_at or job.last_attempt_at
                self.queue.update(job)
                self.history.record(job)
                return job

            job.increment_retry()
            job.status = JobStatus.PENDING  # re-queue for retry
            self.queue.update(job)
            backoff = min(
                self.base_retry_delay_seconds * (2 ** (job.retries - 1)),
                self.max_retry_delay_seconds,
            )
            await asyncio.sleep(backoff)

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------
    def cancel(self, job_id: str) -> bool:
        """Mark a queued/running job for cancellation. The actual
        transition happens on the next executor iteration."""
        job = self.queue.get(job_id)
        if job is None:
            return False
        if job.is_terminal:
            return False
        self._cancelled.add(job_id)
        task = self._inflight.get(job_id)
        if task is not None:
            task.cancel()
        return True

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def pending_count(self) -> int:
        return self.queue.count_pending()


def _default_history() -> JobHistory:
    from albra_seed.jobs.history import InMemoryJobHistory  # noqa: PLC0415
    return InMemoryJobHistory()


__all__ = ["JobManager", "JobHandler"]
