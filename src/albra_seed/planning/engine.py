"""Planning — typed objectives, subtasks, dependencies, and budgets.

This module provides a planning layer that allows expressing agent
objectives as verifiable task trees with:
- Typed goals and subtasks.
- Dependencies between subtasks.
- Explicit completion criteria.
- Progress tracking.
- Step/time budgets (anti-infinite-loop).
- Output validation.
- Separation of planning, execution, and evaluation.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SubtaskState(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BUDGET_EXCEEDED = "budget_exceeded"


class Subtask(BaseModel):
    subtask_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    description: str = ""
    dependencies: list[str] = Field(default_factory=list)
    completion_criteria: str = ""  # human-readable
    validator: str = ""  # callable name or description
    state: SubtaskState = SubtaskState.PENDING
    output: Any = None
    error: str | None = None
    started_at: float | None = None
    completed_at: float | None = None


class PlanBudget(BaseModel):
    max_steps: int = 50
    max_duration_s: float = 300.0
    max_tool_calls: int = 100


class Plan(BaseModel):
    plan_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    objective: str
    subtasks: list[Subtask] = Field(default_factory=list)
    budget: PlanBudget = Field(default_factory=PlanBudget)
    created_at: float = Field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    steps_taken: int = 0
    tool_calls_made: int = 0
    is_complete: bool = False
    failure_reason: str | None = None

    def add_subtask(self, st: Subtask) -> None:
        self.subtasks.append(st)

    def get_subtask(self, subtask_id: str) -> Subtask | None:
        for st in self.subtasks:
            if st.subtask_id == subtask_id:
                return st
        return None

    def pending_subtasks(self) -> list[Subtask]:
        return [st for st in self.subtasks if st.state == SubtaskState.PENDING]

    def next_executable(self) -> Subtask | None:
        """Return the next subtask whose dependencies are all completed."""
        for st in self.subtasks:
            if st.state != SubtaskState.PENDING:
                continue
            if all(
                self.get_subtask(dep) and self.get_subtask(dep).state == SubtaskState.COMPLETED
                for dep in st.dependencies
            ):
                return st
        return None

    def check_budget(self) -> bool:
        """Return True if budget allows more steps."""
        if self.steps_taken >= self.budget.max_steps:
            return False
        if self.tool_calls_made >= self.budget.max_tool_calls:
            return False
        return not (self.started_at and time.time() - self.started_at > self.budget.max_duration_s)

    def all_completed(self) -> bool:
        return all(st.state == SubtaskState.COMPLETED for st in self.subtasks)

    def any_failed(self) -> bool:
        return any(st.state == SubtaskState.FAILED for st in self.subtasks)

    def progress_pct(self) -> float:
        if not self.subtasks:
            return 0.0
        completed = sum(1 for st in self.subtasks if st.state == SubtaskState.COMPLETED)
        return (completed / len(self.subtasks)) * 100.0


class PlanExecutor:
    """Executes a Plan by iterating subtasks in dependency order.

    Each subtask is an async callable that receives the plan + subtask
    and returns Any output. The executor validates the output with an
    optional validator callable.
    """

    def __init__(
        self,
        *,
        validators: dict[str, Callable[[Any], bool]] | None = None,
    ) -> None:
        self._validators = validators or {}

    async def execute(
        self,
        plan: Plan,
        callables: dict[str, Callable[[Plan, Subtask], Any]],
    ) -> Plan:
        """Execute the plan. Returns the updated Plan."""
        import asyncio

        plan.started_at = time.time()

        while not plan.all_completed():
            if not plan.check_budget():
                plan.failure_reason = "budget exceeded"
                for st in plan.pending_subtasks():
                    st.state = SubtaskState.BUDGET_EXCEEDED
                break

            st = plan.next_executable()
            if st is None:
                # No executable subtask — either all done or blocked
                if not plan.pending_subtasks():
                    break
                plan.failure_reason = "deadlock: no executable subtask but pending remain"
                break

            st.state = SubtaskState.IN_PROGRESS
            st.started_at = time.time()
            plan.steps_taken += 1

            try:
                callable_ = callables.get(st.name)
                if callable_ is None:
                    st.state = SubtaskState.FAILED
                    st.error = f"no callable for subtask {st.name!r}"
                    continue

                # Execute (support both sync and async callables)
                result = callable_(plan, st)
                if asyncio.iscoroutine(result):
                    result = await result

                st.output = result
                st.completed_at = time.time()

                # Validate output
                if st.validator and st.validator in self._validators and not self._validators[st.validator](result):
                    st.state = SubtaskState.FAILED
                    st.error = "output validation failed"
                    continue

                st.state = SubtaskState.COMPLETED

            except Exception as exc:
                st.state = SubtaskState.FAILED
                st.error = f"{type(exc).__name__}: {exc}"
                st.completed_at = time.time()

        plan.completed_at = time.time()
        plan.is_complete = plan.all_completed() and not plan.any_failed()
        if plan.any_failed() and not plan.failure_reason:
            plan.failure_reason = "one or more subtasks failed"

        return plan


__all__ = [
    "SubtaskState",
    "Subtask",
    "PlanBudget",
    "Plan",
    "PlanExecutor",
]
