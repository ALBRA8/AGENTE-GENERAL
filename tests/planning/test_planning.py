"""Tests for the planning engine.

Covers:
- Plan creation and subtask management.
- Dependency ordering (next_executable).
- Budget enforcement (max_steps, max_tool_calls, max_duration).
- Execution success, failure, deadlock detection.
- Output validation.
- Progress tracking.
- Completion detection.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from albra_seed.planning import (
    Plan,
    PlanBudget,
    PlanExecutor,
    Subtask,
    SubtaskState,
)


class TestPlan:
    def test_add_and_get(self):
        p = Plan(objective="test")
        st = Subtask(name="step1")
        p.add_subtask(st)
        assert p.get_subtask(st.subtask_id) is st

    def test_pending_subtasks(self):
        p = Plan(objective="test")
        p.add_subtask(Subtask(name="a"))
        p.add_subtask(Subtask(name="b", state=SubtaskState.COMPLETED))
        pending = p.pending_subtasks()
        assert len(pending) == 1
        assert pending[0].name == "a"

    def test_next_executable(self):
        p = Plan(objective="test")
        a = Subtask(name="a")
        b = Subtask(name="b", dependencies=[a.subtask_id])
        p.add_subtask(a)
        p.add_subtask(b)
        # Initially, only 'a' is executable (b depends on a)
        assert p.next_executable().name == "a"
        # Complete 'a'
        a.state = SubtaskState.COMPLETED
        assert p.next_executable().name == "b"

    def test_next_executable_blocked(self):
        p = Plan(objective="test")
        a = Subtask(name="a")
        b = Subtask(name="b", dependencies=[a.subtask_id])
        p.add_subtask(a)
        p.add_subtask(b)
        # 'a' is pending, not completed — b is blocked
        a.state = SubtaskState.IN_PROGRESS
        assert p.next_executable() is None

    def test_progress_pct(self):
        p = Plan(objective="test")
        p.add_subtask(Subtask(name="a"))
        p.add_subtask(Subtask(name="b"))
        assert p.progress_pct() == 0.0
        p.subtasks[0].state = SubtaskState.COMPLETED
        assert p.progress_pct() == 50.0

    def test_all_completed(self):
        p = Plan(objective="test")
        p.add_subtask(Subtask(name="a", state=SubtaskState.COMPLETED))
        assert p.all_completed()
        p.add_subtask(Subtask(name="b"))
        assert not p.all_completed()

    def test_any_failed(self):
        p = Plan(objective="test")
        p.add_subtask(Subtask(name="a", state=SubtaskState.COMPLETED))
        assert not p.any_failed()
        p.add_subtask(Subtask(name="b", state=SubtaskState.FAILED))
        assert p.any_failed()


class TestBudget:
    def test_check_budget_ok(self):
        p = Plan(objective="test", budget=PlanBudget(max_steps=10))
        assert p.check_budget()

    def test_max_steps_exceeded(self):
        p = Plan(objective="test", budget=PlanBudget(max_steps=2))
        p.started_at = time.time()
        p.steps_taken = 2
        assert not p.check_budget()

    def test_max_tool_calls_exceeded(self):
        p = Plan(objective="test", budget=PlanBudget(max_tool_calls=5))
        p.started_at = time.time()
        p.tool_calls_made = 5
        assert not p.check_budget()

    def test_max_duration_exceeded(self):
        p = Plan(objective="test", budget=PlanBudget(max_duration_s=0.1))
        p.started_at = time.time() - 1  # 1 second ago
        assert not p.check_budget()


class TestPlanExecutor:
    @pytest.mark.asyncio
    async def test_unknown_dependency_fails_plan_explicitly(self):
        p = Plan(objective="invalid")
        p.add_subtask(Subtask(name="blocked", dependencies=["missing-id"]))

        result = await PlanExecutor().execute(p, {"blocked": lambda plan, st: "should not run"})
        assert not result.is_complete
        assert "unknown dependencies" in result.failure_reason
        assert result.subtasks[0].state == SubtaskState.FAILED

    @pytest.mark.asyncio
    async def test_dependency_cycle_fails_plan_explicitly(self):
        a = Subtask(name="a", subtask_id="a", dependencies=["b"])
        b = Subtask(name="b", subtask_id="b", dependencies=["a"])
        p = Plan(objective="cycle", subtasks=[a, b])

        result = await PlanExecutor().execute(
            p,
            {"a": lambda plan, st: "should not run", "b": lambda plan, st: "should not run"},
        )
        assert not result.is_complete
        assert "cycle" in result.failure_reason
        assert all(st.state == SubtaskState.FAILED for st in result.subtasks)

    @pytest.mark.asyncio
    async def test_simple_success(self):
        p = Plan(objective="greet")
        p.add_subtask(Subtask(name="hello"))

        async def hello_fn(plan, st):
            return "Hi!"

        executor = PlanExecutor()
        result = await executor.execute(p, {"hello": hello_fn})
        assert result.is_complete
        assert result.get_subtask(p.subtasks[0].subtask_id).state == SubtaskState.COMPLETED
        assert result.get_subtask(p.subtasks[0].subtask_id).output == "Hi!"

    @pytest.mark.asyncio
    async def test_dependency_order(self):
        p = Plan(objective="ordered")
        a = Subtask(name="a")
        b = Subtask(name="b", dependencies=[a.subtask_id])
        p.add_subtask(a)
        p.add_subtask(b)

        order = []

        def a_fn(plan, st):
            order.append("a")
            return 1

        def b_fn(plan, st):
            order.append("b")
            return 2

        executor = PlanExecutor()
        await executor.execute(p, {"a": a_fn, "b": b_fn})
        assert order == ["a", "b"]

    @pytest.mark.asyncio
    async def test_failure_marks_plan_incomplete(self):
        p = Plan(objective="test")
        p.add_subtask(Subtask(name="fail"))

        def fail_fn(plan, st):
            raise RuntimeError("boom")

        executor = PlanExecutor()
        result = await executor.execute(p, {"fail": fail_fn})
        assert not result.is_complete
        assert result.any_failed()
        assert "one or more subtasks failed" in (result.failure_reason or "")

    @pytest.mark.asyncio
    async def test_budget_exceeded(self):
        p = Plan(
            objective="test",
            budget=PlanBudget(max_steps=1),
        )
        p.add_subtask(Subtask(name="a"))
        p.add_subtask(Subtask(name="b"))

        def ok_fn(plan, st):
            return "ok"

        executor = PlanExecutor()
        result = await executor.execute(p, {"a": ok_fn, "b": ok_fn})
        assert not result.is_complete
        assert result.failure_reason == "budget exceeded"
        # 'b' should be budget_exceeded
        assert result.subtasks[1].state == SubtaskState.BUDGET_EXCEEDED

    @pytest.mark.asyncio
    async def test_output_validation(self):
        p = Plan(objective="test")
        p.add_subtask(Subtask(name="produce", validator="is_positive"))

        def produce_fn(plan, st):
            return -5  # negative — should fail validation

        executor = PlanExecutor(validators={"is_positive": lambda x: x > 0})
        result = await executor.execute(p, {"produce": produce_fn})
        assert not result.is_complete
        assert result.subtasks[0].state == SubtaskState.FAILED
        assert "validation" in result.subtasks[0].error

    @pytest.mark.asyncio
    async def test_missing_callable(self):
        p = Plan(objective="test")
        p.add_subtask(Subtask(name="no_callable"))

        executor = PlanExecutor()
        result = await executor.execute(p, {})
        assert not result.is_complete
        assert result.subtasks[0].state == SubtaskState.FAILED
        assert "no callable" in result.subtasks[0].error

    @pytest.mark.asyncio
    async def test_progress_tracking(self):
        p = Plan(objective="test")
        p.add_subtask(Subtask(name="a"))
        p.add_subtask(Subtask(name="b"))
        p.add_subtask(Subtask(name="c"))

        def ok_fn(plan, st):
            return "ok"

        executor = PlanExecutor()
        result = await executor.execute(p, {"a": ok_fn, "b": ok_fn, "c": ok_fn})
        assert result.is_complete
        assert result.progress_pct() == 100.0
        assert result.steps_taken == 3


__all__ = [
    "TestPlan",
    "TestBudget",
    "TestPlanExecutor",
]
