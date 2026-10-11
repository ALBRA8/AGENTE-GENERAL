"""Unit tests for the scheduler subpackage."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from albra_seed.core.types import utc_now
from albra_seed.scheduler import (
    CronTrigger,
    IntervalTrigger,
    OneShotTrigger,
    Scheduler,
)


# -------------------------------------------------------------------
# 1. Scheduler schedules one-shot task and dispatches it
# -------------------------------------------------------------------


def test_scheduler_schedules_one_shot():
    fired = []

    async def dispatch(task):
        fired.append(task.target)

    sched = Scheduler(dispatch_fn=dispatch)
    # Schedule for the past so tick() dispatches immediately.
    past = utc_now() - timedelta(seconds=1)
    task = sched.schedule("echo_task", past, name="one-shot")
    assert task.id in [t.id for t in sched.list_scheduled()]
    assert sched.count() == 1

    n = asyncio.run(sched.tick())
    assert n == 1
    assert fired == ["echo_task"]

    # One-shot trigger is now consumed.
    assert task.next_run is None


# -------------------------------------------------------------------
# 2. Scheduler cancel removes a scheduled task
# -------------------------------------------------------------------


def test_scheduler_cancel():
    sched = Scheduler()
    when = utc_now() + timedelta(seconds=60)
    task = sched.schedule("future_task", when, name="later")
    assert sched.count() == 1

    removed = sched.cancel(task.id)
    assert removed is True
    assert sched.count() == 0
    assert sched.list_scheduled() == []

    # Cancelling again returns False.
    assert sched.cancel(task.id) is False


# -------------------------------------------------------------------
# 3. Scheduler lists scheduled tasks
# -------------------------------------------------------------------


def test_scheduler_lists_scheduled():
    sched = Scheduler()
    t1 = sched.schedule("a", utc_now() - timedelta(seconds=1))
    t2 = sched.schedule("b", utc_now() + timedelta(seconds=60))

    listed = sched.list_scheduled()
    assert len(listed) == 2
    targets = {t.target for t in listed}
    assert targets == {"a", "b"}
    assert sched.count() == 2


# -------------------------------------------------------------------
# 4. Scheduler recurring task fires repeatedly
# -------------------------------------------------------------------


def test_scheduler_recurring_task():
    fired = []

    async def dispatch(task):
        fired.append(task.run_count)

    sched = Scheduler(dispatch_fn=dispatch, tick_interval_seconds=0.01)
    # Tiny interval so we can fire several times quickly.
    task = sched.schedule_recurring(
        "recurrent", interval_seconds=0.01, start_at=utc_now()
    )
    # Fire several ticks manually (no background loop).
    for _ in range(3):
        asyncio.run(sched.tick())
        asyncio.run(asyncio.sleep(0.012))

    assert task.run_count >= 1
    assert len(fired) >= 1


# -------------------------------------------------------------------
# 5. OneShotTrigger next_run behavior
# -------------------------------------------------------------------


def test_one_shot_trigger():
    when = utc_now() + timedelta(seconds=10)
    trig = OneShotTrigger(fire_at=when)
    assert trig.is_one_shot

    # Before fire time -> next_run is fire_at.
    assert trig.next_run(after=utc_now()) == when

    # After fire time -> None (already past).
    assert trig.next_run(after=when + timedelta(seconds=1)) is None

    # After mark_fired -> None.
    trig.mark_fired()
    assert trig.next_run(after=utc_now()) is None


# -------------------------------------------------------------------
# 6. IntervalTrigger computes next run correctly
# -------------------------------------------------------------------


def test_interval_trigger():
    start = utc_now()
    trig = IntervalTrigger(interval_seconds=60.0, start_at=start)

    # At start, next run is start.
    n1 = trig.next_run(after=start - timedelta(seconds=1))
    assert n1 == start

    # After 90 seconds, next run is start + 120s (skips already-passed tick at 60).
    after = start + timedelta(seconds=90)
    n2 = trig.next_run(after=after)
    expected = start + timedelta(seconds=120)
    # Compare to the second to avoid microsecond drift.
    assert abs((n2 - expected).total_seconds()) < 1.0


# -------------------------------------------------------------------
# 7. CronTrigger fallback parser works for simple expressions
# -------------------------------------------------------------------


def test_cron_trigger_fallback():
    # Every minute.
    trig = CronTrigger(expression="* * * * *")
    now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    nxt = trig.next_run(after=now)
    assert nxt is not None
    # Next run is the next minute boundary.
    assert nxt.minute == 1
    assert nxt.hour == 12

    # Every 5 minutes.
    trig5 = CronTrigger(expression="*/5 * * * *")
    nxt = trig5.next_run(after=datetime(2024, 1, 1, 12, 2, 0, tzinfo=timezone.utc))
    assert nxt is not None
    assert nxt.minute == 5
