"""Job — a unit of asynchronous work submitted by workflows or external callers.

A Job moves through a well-defined lifecycle:

    PENDING → RUNNING → SUCCEEDED | FAILED | CANCELLED

Retries bump `retries` (capped at `max_retries`). On terminal transition
the `finished_at` timestamp is set. The optional `execution_id` ties a
job to the ExecutionRunner trace that ran it.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now, new_id


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class Job(BaseModel):
    """A unit of asynchronous work."""

    id: str = Field(default_factory=lambda: new_id("job"))
    type: str = "generic"
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    retries: int = 0
    max_retries: int = 0

    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_attempt_at: datetime | None = None

    result: Any = None
    error: str | None = None
    execution_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------
    def mark_running(self) -> None:
        self.status = JobStatus.RUNNING
        self.started_at = utc_now()
        self.last_attempt_at = self.started_at

    def mark_succeeded(self, result: Any = None) -> None:
        if result is not None:
            self.result = result
        self.status = JobStatus.SUCCEEDED
        self.finished_at = utc_now()

    def mark_failed(self, error: str) -> None:
        self.error = error
        self.status = JobStatus.FAILED
        self.finished_at = utc_now()

    def mark_cancelled(self) -> None:
        self.status = JobStatus.CANCELLED
        self.finished_at = utc_now()

    def mark_timeout(self) -> None:
        self.status = JobStatus.TIMEOUT
        self.finished_at = utc_now()

    def increment_retry(self) -> int:
        self.retries += 1
        self.last_attempt_at = utc_now()
        return self.retries

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMEOUT,
        }

    @property
    def can_retry(self) -> bool:
        return self.status in {JobStatus.FAILED, JobStatus.TIMEOUT} and self.retries < self.max_retries

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = ["Job", "JobStatus"]
