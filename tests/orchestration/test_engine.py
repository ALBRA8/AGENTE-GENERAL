"""Tests for the agent orchestration engine.

Covers:
- Sequential execution
- Parallel execution
- Dependency propagation
- Cycle detection
- Concurrency limits
- Timeout handling
- Retry with backoff
- Required vs optional dependency failure
- Cancellation propagation
- Result tracking
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from albra_seed.orchestration import (
    Orchestrator,
    RetryPolicy,
    Task,
    TaskGraph,
    TaskState,
)


# =====================================================================
# Helpers
# =====================================================================


async def _identity(deps: dict[str, Any]) -> str:
    return "ok"


async def _fail(deps: dict[str, Any]) -> str:
    raise RuntimeError("intentional failure")


async def _sum_deps(deps: dict[str, Any]) -> int:
    return sum(v for v in deps.values() if isinstance(v, (int, float)))


async def _slow(deps: dict[str, Any]) -> str:
    await asyncio.sleep(10)
    return "slow"


# =====================================================================
# Tests
# =====================================================================


class TestTaskGraph:
    def test_add_and_get(self):
        g = TaskGraph()
        t = Task(name="test", task_id="t1")
        g.add_task(t)
        assert g.get("t1") is t

    def test_duplicate_raises(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="t1"))
        with pytest.raises(ValueError, match="already exists"):
            g.add_task(Task(name="b", task_id="t1"))

    def test_cycle_detection(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a", dependencies=["b"]))
        g.add_task(Task(name="b", task_id="b", dependencies=["a"]))
        with pytest.raises(ValueError, match="Cycle detected"):
            g.validate()

    def test_missing_dependency(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a", dependencies=["missing"]))
        with pytest.raises(ValueError, match="unknown task"):
            g.validate()

    def test_topological_order(self):
        g = TaskGraph()
        g.add_task(Task(name="c", task_id="c", dependencies=["a", "b"]))
        g.add_task(Task(name="a", task_id="a"))
        g.add_task(Task(name="b", task_id="b", dependencies=["a"]))
        order = g.topological_order()
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_roots(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a"))
        g.add_task(Task(name="b", task_id="b", dependencies=["a"]))
        roots = g.roots()
        assert len(roots) == 1
        assert roots[0].task_id == "a"


class TestSequentialExecution:
    @pytest.mark.asyncio
    async def test_single_task(self):
        g = TaskGraph()
        g.add_task(Task(name="only", task_id="t1"))
        orch = Orchestrator()
        result = await orch.execute(g, {"t1": _identity})
        assert result.success
        assert result.get("t1").state == TaskState.COMPLETED
        assert result.get("t1").output == "ok"

    @pytest.mark.asyncio
    async def test_sequential_chain(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a"))
        g.add_task(Task(name="b", task_id="b", dependencies=["a"]))
        g.add_task(Task(name="c", task_id="c", dependencies=["b"]))
        orch = Orchestrator()
        result = await orch.execute(g, {"a": _identity, "b": _identity, "c": _identity})
        assert result.success
        for tid in ("a", "b", "c"):
            assert result.get(tid).state == TaskState.COMPLETED


class TestParallelExecution:
    @pytest.mark.asyncio
    async def test_parallel_independent(self):
        """Two independent tasks run in parallel — total time ≈ max, not sum."""
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a", timeout=5))
        g.add_task(Task(name="b", task_id="b", timeout=5))

        async def sleep_1s(deps):
            await asyncio.sleep(1)
            return "a"

        async def sleep_1s_b(deps):
            await asyncio.sleep(1)
            return "b"

        orch = Orchestrator(max_concurrency=2)
        t0 = time.monotonic()
        result = await orch.execute(g, {"a": sleep_1s, "b": sleep_1s_b})
        elapsed = time.monotonic() - t0
        assert result.success
        # Should take ~1s (parallel), not ~2s (sequential)
        assert elapsed < 1.8, f"Expected parallel, took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_concurrency_limit(self):
        """With max_concurrency=1, tasks run sequentially."""
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a"))
        g.add_task(Task(name="b", task_id="b"))

        call_count = {"n": 0}
        active = {"n": 0}
        max_active = {"n": 0}

        async def tracked(deps):
            call_count["n"] += 1
            active["n"] += 1
            max_active["n"] = max(max_active["n"], active["n"])
            await asyncio.sleep(0.1)
            active["n"] -= 1
            return "ok"

        orch = Orchestrator(max_concurrency=1)
        await orch.execute(g, {"a": tracked, "b": tracked})
        assert max_active["n"] == 1, "Only one task should be active at a time"


class TestDependencyPropagation:
    @pytest.mark.asyncio
    async def test_results_passed_to_dependents(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a"))
        g.add_task(Task(name="b", task_id="b", dependencies=["a"]))
        g.add_task(Task(name="c", task_id="c", dependencies=["b"]))

        async def producer(deps):
            return 10

        async def doubler(deps):
            return deps["a"] * 2

        async def adder(deps):
            return deps["b"] + 1

        orch = Orchestrator()
        result = await orch.execute(g, {"a": producer, "b": doubler, "c": adder})
        assert result.get("c").output == 21  # (10*2)+1

    @pytest.mark.asyncio
    async def test_multiple_deps_combined(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a"))
        g.add_task(Task(name="b", task_id="b"))
        g.add_task(Task(name="c", task_id="c", dependencies=["a", "b"]))

        async def ten(deps):
            return 10

        async def twenty(deps):
            return 20

        orch = Orchestrator()
        result = await orch.execute(g, {"a": ten, "b": twenty, "c": _sum_deps})
        assert result.get("c").output == 30


class TestFailureHandling:
    @pytest.mark.asyncio
    async def test_required_failure_cancels_dependents(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a", required=True))
        g.add_task(Task(name="b", task_id="b", dependencies=["a"]))

        orch = Orchestrator()
        result = await orch.execute(g, {"a": _fail, "b": _identity})
        assert not result.success
        assert result.get("a").state == TaskState.FAILED
        assert result.get("b").state == TaskState.CANCELLED
        assert "a" in result.failed_required

    @pytest.mark.asyncio
    async def test_optional_failure_skips_dependents(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a", required=False))
        g.add_task(Task(name="b", task_id="b", dependencies=["a"]))

        orch = Orchestrator()
        result = await orch.execute(g, {"a": _fail, "b": _identity})
        assert result.get("a").state == TaskState.FAILED
        assert result.get("b").state == TaskState.SKIPPED
        assert "b" not in result.failed_required

    @pytest.mark.asyncio
    async def test_independent_task_succeeds_after_failure(self):
        """A failure in one branch doesn't block independent tasks."""
        g = TaskGraph()
        g.add_task(Task(name="fail", task_id="fail", required=False))
        g.add_task(Task(name="ok", task_id="ok"))

        orch = Orchestrator()
        result = await orch.execute(g, {"fail": _fail, "ok": _identity})
        assert result.get("ok").state == TaskState.COMPLETED
        assert result.get("ok").output == "ok"


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_marks_failed(self):
        g = TaskGraph()
        g.add_task(Task(name="slow", task_id="slow", timeout=0.1))

        orch = Orchestrator()
        result = await orch.execute(g, {"slow": _slow})
        assert result.get("slow").state == TaskState.FAILED
        assert "timeout" in result.get("slow").error


class TestRetry:
    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        g = TaskGraph()
        g.add_task(Task(
            name="flaky",
            task_id="flaky",
            retry=RetryPolicy(max_attempts=3, backoff_base=0.01),
        ))

        call_count = {"n": 0}

        async def flaky(deps):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise RuntimeError("transient")
            return "recovered"

        orch = Orchestrator()
        result = await orch.execute(g, {"flaky": flaky})
        assert result.get("flaky").state == TaskState.COMPLETED
        assert result.get("flaky").output == "recovered"
        assert result.get("flaky").attempts == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_first_attempt_success(self):
        g = TaskGraph()
        g.add_task(Task(
            name="ok",
            task_id="ok",
            retry=RetryPolicy(max_attempts=3),
        ))

        orch = Orchestrator()
        result = await orch.execute(g, {"ok": _identity})
        assert result.get("ok").attempts == 1

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        g = TaskGraph()
        g.add_task(Task(
            name="always-fail",
            task_id="always-fail",
            retry=RetryPolicy(max_attempts=2, backoff_base=0.01),
        ))

        orch = Orchestrator()
        result = await orch.execute(g, {"always-fail": _fail})
        assert result.get("always-fail").state == TaskState.FAILED
        assert result.get("always-fail").attempts == 2


class TestResultTracking:
    @pytest.mark.asyncio
    async def test_duration_recorded(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a"))

        async def sleep_50ms(deps):
            await asyncio.sleep(0.05)
            return "ok"

        orch = Orchestrator()
        result = await orch.execute(g, {"a": sleep_50ms})
        assert result.get("a").duration_ms is not None
        assert result.get("a").duration_ms >= 40  # at least ~50ms

    @pytest.mark.asyncio
    async def test_total_duration_recorded(self):
        g = TaskGraph()
        g.add_task(Task(name="a", task_id="a"))
        orch = Orchestrator()
        result = await orch.execute(g, {"a": _identity})
        assert result.total_duration_ms is not None
        assert result.total_duration_ms >= 0


__all__ = [
    "TestTaskGraph",
    "TestSequentialExecution",
    "TestParallelExecution",
    "TestDependencyPropagation",
    "TestFailureHandling",
    "TestTimeout",
    "TestRetry",
    "TestResultTracking",
]
