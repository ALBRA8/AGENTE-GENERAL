"""Unit tests for the jobs subpackage."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from albra_seed.jobs import (
    InMemoryJobHistory,
    InMemoryJobQueue,
    Job,
    JobManager,
    JobStatus,
    SQLiteJobHistory,
    SQLiteJobQueue,
)


# -------------------------------------------------------------------
# 1. JobManager queues and executes a job
# -------------------------------------------------------------------


def test_job_manager_queues_and_executes():
    manager = JobManager()
    seen = []

    async def echo_handler(job: Job):
        seen.append(job.id)
        return {"echoed": job.payload.get("message")}

    manager.register_handler("echo", echo_handler)
    job = manager.submit(type="echo", payload={"message": "hi"})
    assert manager.pending_count() == 1

    # Execute one (drains queue).
    asyncio.run(manager.run_one())

    assert manager.pending_count() == 0
    assert seen == [job.id]
    final = manager.get(job.id)
    assert final is not None
    assert final.status == JobStatus.SUCCEEDED
    assert final.result == {"echoed": "hi"}
    assert final.finished_at is not None


# -------------------------------------------------------------------
# 2. Job retries on failure
# -------------------------------------------------------------------


def test_job_retry_on_failure():
    manager = JobManager(base_retry_delay_seconds=0.001, max_retry_delay_seconds=0.01)
    calls = {"n": 0}

    async def flaky_handler(job: Job):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return {"ok": True}

    manager.register_handler("flaky", flaky_handler)
    job = manager.submit(type="flaky", payload={}, max_retries=3)

    # Need to call run_one repeatedly because each retry re-queues.
    async def drain():
        for _ in range(5):
            await manager.run_one()
    asyncio.run(drain())

    assert calls["n"] == 3
    final = manager.get(job.id)
    assert final is not None
    assert final.status == JobStatus.SUCCEEDED
    assert final.retries == 2  # 2 retries + 1 initial attempt = 3 calls


# -------------------------------------------------------------------
# 3. Job cancellation
# -------------------------------------------------------------------


def test_job_cancellation():
    manager = JobManager()
    started = asyncio.Event()

    async def slow_handler(job: Job):
        started.set()
        await asyncio.sleep(5)  # would block test
        return {"ok": True}

    manager.register_handler("slow", slow_handler)
    job = manager.submit(type="slow", payload={})

    async def cancel_after_start():
        # Start the job, then cancel.
        task = asyncio.create_task(manager.run_one())
        await started.wait()
        manager.cancel(job.id)
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_after_start())

    final = manager.get(job.id)
    assert final is not None
    assert final.status == JobStatus.CANCELLED
    assert final.finished_at is not None


# -------------------------------------------------------------------
# 4. Job history persists
# -------------------------------------------------------------------


def test_job_history_persists():
    # Use a temp file for SQLite-backed persistence.
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "jobs.sqlite"
        queue = SQLiteJobQueue(db_path)
        history = SQLiteJobHistory(db_path)
        manager = JobManager(queue=queue, history=history)

        async def echo_handler(job: Job):
            return {"echoed": job.payload.get("message")}

        manager.register_handler("echo", echo_handler)
        job = manager.submit(type="echo", payload={"message": "persist-me"})
        asyncio.run(manager.run_one())

        # New queue/history instances pointing at the SAME file should see the job.
        queue2 = SQLiteJobQueue(db_path)
        history2 = SQLiteJobHistory(db_path)

        # History (separate table) sees the terminal job.
        assert history2.count() == 1
        records = history2.list()
        assert len(records) == 1
        assert records[0].id == job.id
        assert records[0].status == JobStatus.SUCCEEDED
        assert records[0].result == {"echoed": "persist-me"}


# -------------------------------------------------------------------
# 5. SQLiteJobQueue round-trips job state
# -------------------------------------------------------------------


def test_sqlite_queue_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        q = SQLiteJobQueue(Path(tmp) / "q.sqlite")
        j = Job(type="t", payload={"k": 1})
        q.enqueue(j)
        assert q.count_pending() == 1

        # peek does not remove
        peeked = q.peek()
        assert peeked is not None
        assert peeked.id == j.id
        assert q.count_pending() == 1

        # dequeue removes
        out = q.dequeue()
        assert out is not None
        assert out.id == j.id
        assert q.dequeue() is None

        # update flips status
        out.mark_succeeded(result={"done": True})
        q.update(out)
        got = q.get(j.id)
        assert got is not None
        assert got.status == JobStatus.SUCCEEDED
        assert got.result == {"done": True}


# -------------------------------------------------------------------
# 6. InMemoryJobQueue basic ops
# -------------------------------------------------------------------


def test_inmemory_queue_basic_ops():
    q = InMemoryJobQueue()
    j1 = Job(type="t", payload={"i": 1})
    j2 = Job(type="t", payload={"i": 2})
    q.enqueue(j1)
    q.enqueue(j2)
    assert q.count_pending() == 2
    assert q.list() == [j1, j2]

    out1 = q.dequeue()
    assert out1 is not None and out1.id == j1.id
    out2 = q.dequeue()
    assert out2 is not None and out2.id == j2.id
    assert q.dequeue() is None
    assert q.count_pending() == 0

    # remove returns False for unknown id
    assert q.remove("nonexistent") is False
