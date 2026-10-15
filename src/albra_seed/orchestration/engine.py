"""Agent orchestration engine — task graphs with dependencies.

This module provides a modular orchestration layer that integrates with
the existing Runtime. It supports:

- Sequential and parallel task execution.
- Explicit dependency graphs (DAG).
- Execution states: pending, running, completed, failed, cancelled.
- Controlled result propagation between tasks.
- Concurrency limits.
- Timeouts, cancellation, and error handling.
- Configurable retry policies with exponential backoff.
- Cycle detection in dependency graphs.
- Result, error, and duration tracking.
- Clear recovery from partial failures.

The orchestrator does NOT replace Agent.run() — it composes multiple
agent calls (or arbitrary async callables) into a verifiable graph.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger("albra.orchestration")


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class TaskResult(BaseModel):
    task_id: str
    state: TaskState = TaskState.PENDING
    output: Any = None
    error: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    duration_ms: float | None = None
    attempts: int = 0

    @property
    def succeeded(self) -> bool:
        return self.state == TaskState.COMPLETED


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=1, ge=1)
    backoff_base: float = Field(default=0.5, ge=0)
    backoff_cap: float = Field(default=10.0, ge=0)

    def backoff(self, attempt: int) -> float:
        return min(self.backoff_base * (2 ** attempt), self.backoff_cap)


class Task(BaseModel):
    """A single unit of work in the orchestration graph."""
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    callable_desc: str = ""  # human-readable description
    dependencies: list[str] = Field(default_factory=list)
    timeout: float = Field(default=60.0, gt=0)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    required: bool = True  # if True, failure cancels dependents
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Set by the orchestrator at runtime
    result: TaskResult | None = None


class TaskGraph:
    """A directed acyclic graph of tasks with explicit dependencies."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def add_task(self, task: Task) -> None:
        if task.task_id in self._tasks:
            raise ValueError(f"Task {task.task_id!r} already exists")
        self._tasks[task.task_id] = task

    def get(self, task_id: str) -> Task:
        return self._tasks[task_id]

    def all_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    def roots(self) -> list[Task]:
        """Tasks with no dependencies."""
        return [t for t in self._tasks.values() if not t.dependencies]

    def dependents(self, task_id: str) -> list[Task]:
        """Tasks that depend on ``task_id``."""
        return [t for t in self._tasks.values() if task_id in t.dependencies]

    def validate(self) -> None:
        """Check for cycles and missing dependencies. Raises on invalid."""
        # Check all dependencies exist
        for task in self._tasks.values():
            for dep in task.dependencies:
                if dep not in self._tasks:
                    raise ValueError(
                        f"Task {task.task_id!r} depends on unknown task {dep!r}"
                    )

        # Cycle detection via DFS
        white, gray, black = 0, 1, 2
        color: dict[str, int] = dict.fromkeys(self._tasks, white)

        def dfs(tid: str, path: list[str]) -> None:
            color[tid] = gray
            path.append(tid)
            for dep in self._tasks[tid].dependencies:
                if color[dep] == gray:
                    cycle = path[path.index(dep):] + [dep]
                    raise ValueError(f"Cycle detected: {' → '.join(cycle)}")
                if color[dep] == white:
                    dfs(dep, path)
            path.pop()
            color[tid] = black

        for tid in self._tasks:
            if color[tid] == white:
                dfs(tid, [])

    def topological_order(self) -> list[str]:
        """Return task IDs in topological order (Kahn's algorithm)."""
        self.validate()
        in_degree: dict[str, int] = {tid: len(t.dependencies) for tid, t in self._tasks.items()}
        queue: list[str] = [tid for tid, deg in in_degree.items() if deg == 0]
        result: list[str] = []
        while queue:
            tid = queue.pop(0)
            result.append(tid)
            for dep_task in self.dependents(tid):
                in_degree[dep_task.task_id] -= 1
                if in_degree[dep_task.task_id] == 0:
                    queue.append(dep_task.task_id)
        if len(result) != len(self._tasks):
            raise ValueError("Graph has a cycle (topological sort incomplete)")
        return result


class OrchestrationResult(BaseModel):
    """Final result of an orchestration run."""
    orchestration_id: str
    results: dict[str, TaskResult] = Field(default_factory=dict)
    started_at: float
    completed_at: float | None = None
    total_duration_ms: float | None = None
    success: bool = False
    failed_required: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)

    def get(self, task_id: str) -> TaskResult | None:
        return self.results.get(task_id)


class Orchestrator:
    """Executes a TaskGraph with concurrency limits, timeouts, and retries.

    Each task is an async callable that receives a dict of dependency
    results (keyed by dependency task_id) and returns Any output.
    """

    def __init__(
        self,
        *,
        max_concurrency: int = 4,
        default_timeout: float = 60.0,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if default_timeout <= 0:
            raise ValueError("default_timeout must be greater than 0")
        self.max_concurrency = max_concurrency
        self.default_timeout = default_timeout
        self._semaphore: asyncio.Semaphore | None = None

    async def execute(
        self,
        graph: TaskGraph,
        callables: dict[str, Callable[[dict[str, Any]], Coroutine[Any, Any, Any]]],
    ) -> OrchestrationResult:
        """Execute the graph. Returns an OrchestrationResult with per-task status."""
        graph.validate()

        # Verify all tasks have a callable
        for task in graph.all_tasks():
            if task.task_id not in callables:
                raise ValueError(f"No callable provided for task {task.task_id!r}")

        orch_id = uuid.uuid4().hex[:12]
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        results: dict[str, TaskResult] = {}
        started = time.monotonic()

        log.info(
            "orchestration.start",
            orchestration_id=orch_id,
            task_count=len(graph.all_tasks()),
            max_concurrency=self.max_concurrency,
        )

        # Track which tasks are cancelled due to required-dep failure
        cancelled: set[str] = set()

        async def run_task(task: Task) -> TaskResult:
            # Check if any required dependency failed or was cancelled
            for dep_id in task.dependencies:
                dep_result = results.get(dep_id)
                if dep_result is None:
                    # Dependency hasn't run — shouldn't happen with topological order
                    cancelled.add(task.task_id)
                    return TaskResult(
                        task_id=task.task_id,
                        state=TaskState.CANCELLED,
                        error=f"dependency {dep_id!r} did not run",
                    )
                if dep_result.state in (TaskState.FAILED, TaskState.CANCELLED):
                    dep_task = graph.get(dep_id)
                    if dep_task.required:
                        cancelled.add(task.task_id)
                        return TaskResult(
                            task_id=task.task_id,
                            state=TaskState.CANCELLED,
                            error=f"required dependency {dep_id!r} {dep_result.state.value}",
                        )
                    # Optional dependency failed — skip this task too
                    cancelled.add(task.task_id)
                    return TaskResult(
                        task_id=task.task_id,
                        state=TaskState.SKIPPED,
                        error=f"optional dependency {dep_id!r} {dep_result.state.value}",
                    )

            # Collect dependency outputs
            dep_outputs = {
                dep_id: results[dep_id].output
                for dep_id in task.dependencies
                if dep_id in results and results[dep_id].succeeded
            }

            # Execute with retry
            result = TaskResult(task_id=task.task_id, state=TaskState.PENDING)
            for attempt in range(task.retry.max_attempts):
                result.attempts = attempt + 1
                result.state = TaskState.RUNNING
                result.started_at = time.monotonic()

                async with self._semaphore:
                    try:
                        coro = callables[task.task_id](dep_outputs)
                        output = await asyncio.wait_for(
                            coro, timeout=task.timeout or self.default_timeout
                        )
                        result.output = output
                        result.state = TaskState.COMPLETED
                        result.completed_at = time.monotonic()
                        result.duration_ms = (result.completed_at - result.started_at) * 1000
                        result.error = None
                        log.info(
                            "orchestration.task_completed",
                            task_id=task.task_id,
                            attempt=result.attempts,
                            duration_ms=result.duration_ms,
                        )
                        return result
                    except asyncio.TimeoutError:
                        result.error = f"timeout after {task.timeout}s"
                        result.state = TaskState.FAILED
                        result.completed_at = time.monotonic()
                        result.duration_ms = (result.completed_at - result.started_at) * 1000
                        log.warning(
                            "orchestration.task_timeout",
                            task_id=task.task_id,
                            attempt=result.attempts,
                            timeout=task.timeout,
                        )
                    except asyncio.CancelledError:
                        result.state = TaskState.CANCELLED
                        result.completed_at = time.monotonic()
                        result.duration_ms = (result.completed_at - result.started_at) * 1000
                        raise
                    except Exception as exc:
                        result.error = f"{type(exc).__name__}: {exc}"
                        result.state = TaskState.FAILED
                        result.completed_at = time.monotonic()
                        result.duration_ms = (result.completed_at - result.started_at) * 1000
                        log.warning(
                            "orchestration.task_failed",
                            task_id=task.task_id,
                            attempt=result.attempts,
                            error=result.error,
                        )

                # Backoff before retry (if more attempts remain)
                if attempt < task.retry.max_attempts - 1:
                    backoff = task.retry.backoff(attempt)
                    await asyncio.sleep(backoff)

            return result

        # Execute in topological order, parallelizing independent tasks
        # by grouping into "waves" — each wave contains tasks whose
        # dependencies are all in previous waves.
        topo = graph.topological_order()
        completed_ids: set[str] = set()

        while len(completed_ids) < len(topo):
            # Find tasks whose deps are all completed
            wave = []
            for tid in topo:
                if tid in completed_ids:
                    continue
                task = graph.get(tid)
                if all(d in completed_ids for d in task.dependencies):
                    wave.append(tid)

            if not wave:
                # All remaining tasks are cancelled (dep failures)
                for tid in topo:
                    if tid not in completed_ids:
                        results[tid] = TaskResult(
                            task_id=tid,
                            state=TaskState.CANCELLED,
                            error="dependency chain failure",
                        )
                        completed_ids.add(tid)
                break

            # Run the wave in parallel
            wave_tasks = [graph.get(tid) for tid in wave]
            wave_results = await asyncio.gather(
                *[run_task(t) for t in wave_tasks],
                return_exceptions=False,
            )
            for tid, res in zip(wave, wave_results, strict=False):
                results[tid] = res
                completed_ids.add(tid)

        total_duration = (time.monotonic() - started) * 1000
        failed_required = [
            t.task_id for t in graph.all_tasks()
            if t.required and results.get(t.task_id) and results[t.task_id].state in (TaskState.FAILED, TaskState.CANCELLED)
        ]
        skipped = [
            tid for tid, r in results.items()
            if r.state == TaskState.SKIPPED
        ]

        orch_result = OrchestrationResult(
            orchestration_id=orch_id,
            results=results,
            started_at=started,
            completed_at=time.monotonic(),
            total_duration_ms=total_duration,
            success=len(failed_required) == 0,
            failed_required=failed_required,
            skipped=skipped,
        )

        log.info(
            "orchestration.complete",
            orchestration_id=orch_id,
            success=orch_result.success,
            total_duration_ms=total_duration,
            failed_required=failed_required,
            skipped=skipped,
        )

        return orch_result


__all__ = [
    "TaskState",
    "TaskResult",
    "RetryPolicy",
    "Task",
    "TaskGraph",
    "OrchestrationResult",
    "Orchestrator",
]
