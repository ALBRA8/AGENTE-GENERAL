"""WorkflowState — tracks execution position within a WorkflowGraph.

Tracks:
  - current node id (the step being executed or about to be executed)
  - visited nodes (set of step ids already completed)
  - current step output map (step_id -> outputs dict)
  - paused state (boolean) and hitl_pending step id (when interrupted)
  - the workflow's overall status (PENDING / RUNNING / PAUSED /
    SUCCEEDED / FAILED / CANCELLED)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now


class WorkflowRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"  # HITL interrupt
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_RESUME = "awaiting_resume"


class WorkflowState(BaseModel):
    """Mutable per-execution state of a WorkflowGraph run."""

    workflow_name: str
    run_id: str
    status: WorkflowRunStatus = WorkflowRunStatus.PENDING
    current_node: str | None = None
    visited: list[str] = Field(default_factory=list)
    step_outputs: dict[str, Any] = Field(default_factory=dict)
    paused: bool = False
    hitl_pending: str | None = None  # step id awaiting human input
    hitl_inputs: dict[str, Any] = Field(default_factory=dict)  # step_id -> human input
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------
    def enter(self, step_id: str) -> None:
        self.current_node = step_id
        self.status = WorkflowRunStatus.RUNNING

    def complete(self, step_id: str, outputs: dict[str, Any] | None = None) -> None:
        if step_id not in self.visited:
            self.visited.append(step_id)
        if outputs is not None:
            self.step_outputs[step_id] = outputs
        self.current_node = None

    def pause_for_hitl(self, step_id: str) -> None:
        self.paused = True
        self.hitl_pending = step_id
        self.status = WorkflowRunStatus.PAUSED

    def resume(self, *, human_input: dict[str, Any] | None = None) -> None:
        if self.hitl_pending is None:
            return
        if human_input is not None:
            self.hitl_inputs[self.hitl_pending] = human_input
        self.paused = False
        self.status = WorkflowRunStatus.AWAITING_RESUME
        self.hitl_pending = None

    def fail(self, error: str) -> None:
        self.errors.append(error)
        self.status = WorkflowRunStatus.FAILED
        self.finished_at = utc_now()

    def succeed(self) -> None:
        self.status = WorkflowRunStatus.SUCCEEDED
        self.finished_at = utc_now()

    def cancel(self) -> None:
        self.status = WorkflowRunStatus.CANCELLED
        self.finished_at = utc_now()

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            WorkflowRunStatus.SUCCEEDED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CANCELLED,
        }

    def snapshot(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = ["WorkflowState", "WorkflowRunStatus"]
