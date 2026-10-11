"""Scheduler — schedules future tasks (one-shot and recurring).

The Scheduler is universal: it has NO domain knowledge. It only knows
how to compute the next run time of a Trigger and dispatch to a caller-
supplied async callback. Persistent scheduling (across restarts) is left
to specialization layers — the in-memory store is sufficient for tests
and CORE_ONLY mode.

Methods:
  schedule(task, when)              — one-shot at fixed datetime
  schedule_recurring(task, interval) — recurring every N seconds
  schedule_with_trigger(task, trigger)
  cancel(job_id)
  list_scheduled()
  tick(now=None)                   — dispatch due tasks (returns # dispatched)
  start() / stop()                  — background loop control
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from albra_seed.core.types import new_id, utc_now
from albra_seed.scheduler.task import ScheduledTask
from albra_seed.scheduler.trigger import (
    IntervalTrigger,
    OneShotTrigger,
    Trigger,
)


DispatchFn = Callable[[ScheduledTask], Awaitable[Any]]


class Scheduler:
    """Universal task scheduler. NO domain tasks."""

    def __init__(
        self,
        *,
        dispatch_fn: DispatchFn | None = None,
        tick_interval_seconds: float = 0.5,
    ) -> None:
        self._tasks: dict[str, ScheduledTask] = {}
        self._dispatch_fn = dispatch_fn
        self.tick_interval_seconds = tick_interval_seconds
        self._running = False
        self._loop_task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------
    def schedule(
        self,
        target: str,
        when: datetime,
        *,
        name: str = "",
        target_type: str = "job",
        payload: dict[str, Any] | None = None,
    ) -> ScheduledTask:
        trigger = OneShotTrigger(fire_at=when)
        task = ScheduledTask(
            id=new_id("sched"),
            name=name or f"{target}@{when.isoformat()}",
            target=target,
            target_type=target_type,
            payload=dict(payload or {}),
            trigger=trigger,
        )
        task.recompute_next_run()
        self._tasks[task.id] = task
        return task

    def schedule_recurring(
        self,
        target: str,
        interval_seconds: float,
        *,
        name: str = "",
        target_type: str = "job",
        payload: dict[str, Any] | None = None,
        start_at: datetime | None = None,
    ) -> ScheduledTask:
        trigger = IntervalTrigger(
            interval_seconds=interval_seconds,
            start_at=start_at or utc_now(),
        )
        task = ScheduledTask(
            id=new_id("sched"),
            name=name or f"{target}~every{int(interval_seconds)}s",
            target=target,
            target_type=target_type,
            payload=dict(payload or {}),
            trigger=trigger,
        )
        task.recompute_next_run()
        self._tasks[task.id] = task
        return task

    def schedule_with_trigger(
        self,
        target: str,
        trigger: Trigger,
        *,
        name: str = "",
        target_type: str = "job",
        payload: dict[str, Any] | None = None,
    ) -> ScheduledTask:
        task = ScheduledTask(
            id=new_id("sched"),
            name=name or target,
            target=target,
            target_type=target_type,
            payload=dict(payload or {}),
            trigger=trigger,
        )
        task.recompute_next_run()
        self._tasks[task.id] = task
        return task

    # ------------------------------------------------------------------
    # Cancellation + listing
    # ------------------------------------------------------------------
    def cancel(self, task_id: str) -> bool:
        return self._tasks.pop(task_id, None) is not None

    def disable(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.enabled = False
        task.next_run = None
        return True

    def enable(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.enabled = True
        task.recompute_next_run()
        return True

    def list_scheduled(self) -> list[ScheduledTask]:
        return list(self._tasks.values())

    def get(self, task_id: str) -> ScheduledTask | None:
        return self._tasks.get(task_id)

    def count(self) -> int:
        return len(self._tasks)

    # ------------------------------------------------------------------
    # Dispatch loop
    # ------------------------------------------------------------------
    async def tick(self, *, now: datetime | None = None) -> int:
        """Dispatch all due tasks. Returns the number dispatched."""
        now = now or utc_now()
        due: list[ScheduledTask] = []
        for task in list(self._tasks.values()):
            if not task.enabled or task.next_run is None:
                continue
            if task.next_run <= now:
                due.append(task)
        dispatched = 0
        for task in due:
            await self._dispatch(task)
            dispatched += 1
        return dispatched

    async def _dispatch(self, task: ScheduledTask) -> None:
        if self._dispatch_fn is not None:
            try:
                await self._dispatch_fn(task)
            except Exception:  # noqa: BLE001
                # Dispatch errors never break the scheduler loop.
                pass
        task.mark_run()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(self.tick_interval_seconds)


__all__ = ["Scheduler", "DispatchFn"]
