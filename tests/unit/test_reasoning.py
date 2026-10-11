"""Unit tests for the reasoning + context subpackages (F2-B).

Covers:
  * DefaultReasoningEngine.decompose() in CORE_ONLY mode (rule-based)
  * Planner.produce plan with correct dependency ordering
  * PromptManager rendering of built-in templates
  * ContextManager propagating correlation_id across the tracker
"""

from __future__ import annotations

import pytest

from albra_seed.context import ContextManager, CorrelationTracker
from albra_seed.core.types import AutonomyLevel, RiskLevel
from albra_seed.reasoning import (
    BUILTIN_TEMPLATES,
    DefaultReasoningEngine,
    Planner,
    Plan,
    Decomposition,
    PriorityItem,
    PromptManager,
    ReasoningEngine,
    ValidationResult,
)


# =====================================================================
# TEST 1 — DefaultReasoningEngine decomposes a task (CORE_ONLY)
# =====================================================================


async def test_default_engine_decomposes_task() -> None:
    """In CORE_ONLY mode (no provider), decompose returns the canonical
    skeleton of steps — non-empty and structurally valid."""
    engine = DefaultReasoningEngine()  # no provider
    decomp = await engine.decompose("write a report")
    assert isinstance(decomp, Decomposition)
    assert decomp.task == "write a report"
    assert len(decomp.steps) >= 4  # canonical skeleton
    titles = [s.title for s in decomp.steps]
    assert "gather_context" in titles
    assert "respond" in titles
    # The 'respond' step depends on 'synthesize' in the canonical skeleton.
    respond = next(s for s in decomp.steps if s.title == "respond")
    assert "synthesize" in respond.depends_on


# =====================================================================
# TEST 2 — Planner produces a Plan
# =====================================================================


async def test_planner_produces_plan() -> None:
    """Planner.plan returns a Plan with steps in dependency order."""
    planner = Planner()
    plan = await planner.plan("summarize a document")
    assert isinstance(plan, Plan)
    assert plan.steps  # non-empty
    # The first step must have no unresolved dependencies (it's first).
    first = plan.steps[0]
    assert first.depends_on == []
    # Each subsequent step's dependencies appear earlier in the plan.
    prior_ids = {first.step_id}
    for step in plan.steps[1:]:
        for dep in step.depends_on:
            assert dep in prior_ids, (
                f"step {step.title} depends on {dep} which is not earlier"
            )
        prior_ids.add(step.step_id)
    # Final step is 'respond'.
    assert plan.steps[-1].title == "respond"


# =====================================================================
# TEST 3 — PromptManager renders built-in templates
# =====================================================================


def test_prompt_manager_renders_template() -> None:
    """Built-in templates load and render with provided variables."""
    pm = PromptManager()
    assert "system_base" in pm.list_templates()
    assert "task_decomposition" in pm.list_templates()
    assert "tool_selection" in pm.list_templates()
    rendered = pm.render("system_base", {
        "agent_name": "test-agent",
        "autonomy_level": "L1",
        "session_id": "sess-1",
    })
    assert "test-agent" in rendered
    assert "L1" in rendered
    assert "sess-1" in rendered
    # Built-ins cannot be overridden without override=True.
    with pytest.raises(ValueError):
        pm.register("system_base", "new")
    # With override=True it works.
    pm.register("system_base", "overridden", override=True)
    assert pm.get("system_base") == "overridden"


# =====================================================================
# TEST 4 — ContextManager propagates correlation_id to the tracker
# =====================================================================


async def test_context_manager_propagates_correlation_id() -> None:
    """When a context is created, CorrelationTracker.current_* should
    return the same ids, and after closing they revert to None."""
    cm = ContextManager()
    ctx = cm.create(
        agent_id="a1",
        task="do something",
        autonomy_level=AutonomyLevel.L1_RECOMMEND,
        risk_level=RiskLevel.LOW,
    )
    # Inside the scope, the tracker should see the same ids.
    assert CorrelationTracker.current_correlation_id() == ctx.correlation_id
    assert CorrelationTracker.current_execution_id() == ctx.execution_id
    assert CorrelationTracker.current_agent_id() == "a1"

    # A child context should inherit the parent's correlation_id.
    child = cm.create_child(ctx.execution_id, agent_id="a1", task="child task")
    assert child.correlation_id == ctx.correlation_id
    assert child.parent_execution_id == ctx.execution_id

    # Close the parent context — tracker should reset.
    cm.close(ctx.execution_id)
    assert CorrelationTracker.current_execution_id() is None


# =====================================================================
# TEST 5 — DefaultReasoningEngine prioritizes by weight
# =====================================================================


async def test_default_engine_prioritizes_by_weight() -> None:
    """prioritize() orders items by descending weight with id tiebreak."""
    engine = DefaultReasoningEngine()
    items = [
        PriorityItem(item_id="b", title="B", weight=0.5),
        PriorityItem(item_id="a", title="A", weight=1.0),
        PriorityItem(item_id="c", title="C", weight=1.0),
    ]
    pri = await engine.prioritize(items)
    assert pri.ordering[0] == "a"  # weight=1.0, id="a" < "c"
    assert pri.ordering[1] == "c"  # weight=1.0, id="c" > "a"
    assert pri.ordering[2] == "b"  # weight=0.5


# =====================================================================
# TEST 6 — DefaultReasoningEngine validates results structurally
# =====================================================================


async def test_default_engine_validates_results() -> None:
    """validate() accepts non-empty data and rejects None / empty."""
    engine = DefaultReasoningEngine()
    ok = await engine.validate({"k": "v"})
    assert isinstance(ok, ValidationResult)
    assert ok.is_valid is True

    bad_none = await engine.validate(None)
    assert bad_none.is_valid is False
    assert any("None" in e for e in bad_none.errors)

    bad_empty = await engine.validate("")
    assert bad_empty.is_valid is False

    bad_empty_list = await engine.validate([])
    assert bad_empty_list.is_valid is False


# =====================================================================
# TEST 7 — ReasoningEngine abstract base
# =====================================================================


def test_reasoning_engine_is_abstract() -> None:
    """ReasoningEngine cannot be instantiated directly."""
    with pytest.raises(TypeError):
        ReasoningEngine()  # type: ignore[abstract]


# =====================================================================
# TEST 8 — BUILTIN_TEMPLATES lists the expected templates
# =====================================================================


def test_builtin_templates_listed() -> None:
    """BUILTIN_TEMPLATES contains exactly the three built-in names."""
    assert set(BUILTIN_TEMPLATES) == {
        "system_base",
        "task_decomposition",
        "tool_selection",
    }
