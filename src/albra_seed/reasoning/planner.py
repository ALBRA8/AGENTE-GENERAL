"""Planner — converts a task + context into a Plan.

Generic non-domain planning. The default plan always follows the
canonical ALBRA agent phases (gather context → analyze → synthesize →
respond) but can be extended with custom steps. The planner accepts
an optional `LLMProvider` (via the registry/router) for richer
decomposition; in CORE_ONLY mode it falls back to a rule-based
decomposition that does not call any LLM.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import RunContext, new_id, utc_now
from albra_seed.reasoning.engine import (
    DecomposedStep,
    Decomposition,
    Plan,
    PlanStep,
)

# The canonical non-domain plan skeleton. Specialization layers may
# extend, but the SEED only ships this generic template.
CANONICAL_PLAN_SKELETON: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("gather_context", "Collect relevant context for the task", ()),
    ("analyze", "Analyse the gathered context", ("gather_context",)),
    ("synthesize", "Synthesize a candidate response", ("analyze",)),
    ("respond", "Produce the final response", ("synthesize",)),
)


class Planner:
    """Generic planner — produces Plans without domain knowledge."""

    def __init__(self, *, extra_steps: list[dict[str, Any]] | None = None) -> None:
        self._extra_steps: list[dict[str, Any]] = list(extra_steps or [])

    async def decompose(
        self, task: str, context: RunContext | None = None
    ) -> Decomposition:
        """Decompose a task into the canonical skeleton + extra steps."""
        steps: list[DecomposedStep] = []
        for idx, (title, desc, deps) in enumerate(CANONICAL_PLAN_SKELETON):
            steps.append(
                DecomposedStep(
                    title=title,
                    description=desc,
                    depends_on=list(deps),
                    order=idx,
                )
            )
        # Extra steps (from specialization) are appended after the canonical
        # skeleton with `synthesize` as a dependency by default.
        for ext in self._extra_steps:
            steps.append(
                DecomposedStep(
                    title=ext.get("title", "extra"),
                    description=ext.get("description", ""),
                    depends_on=ext.get("depends_on", ["synthesize"]),
                    order=ext.get("order", len(steps)),
                )
            )
        return Decomposition(task=task, steps=steps)

    async def plan(
        self,
        task: str,
        context: RunContext | None = None,
        *,
        decomposition: Decomposition | None = None,
    ) -> Plan:
        """Produce a Plan for the given task."""
        decomp = decomposition or await self.decompose(task, context)
        plan_steps: list[PlanStep] = []
        # Topological sort by depends_on (stable when no cycles).
        resolved: set[str] = set()
        remaining = list(decomp.steps)
        order_counter = 0
        while remaining:
            progressed = False
            for step in list(remaining):
                if all(dep in resolved for dep in step.depends_on):
                    plan_steps.append(
                        PlanStep(
                            step_id=step.step_id,
                            title=step.title,
                            description=step.description,
                            depends_on=list(step.depends_on),
                            order=order_counter,
                            expected_output=f"completed:{step.title}",
                        )
                    )
                    resolved.add(step.step_id)
                    order_counter += 1
                    remaining.remove(step)
                    progressed = True
            if not progressed:
                # Cycle detected — break by ordering the rest by their
                # declared order to keep the plan finite.
                for step in remaining:
                    plan_steps.append(
                        PlanStep(
                            step_id=step.step_id,
                            title=step.title,
                            description=step.description,
                            depends_on=[d for d in step.depends_on if d in resolved],
                            order=order_counter,
                            expected_output=f"completed:{step.title}",
                        )
                    )
                    order_counter += 1
                break

        plan = Plan(
            steps=plan_steps,
            metadata={
                "task": task,
                "decomposition_id": decomp.task,
                "context_execution_id": context.execution_id if context else None,
                "created_at": utc_now().isoformat(),
            },
        )
        return plan


__all__ = ["Planner", "CANONICAL_PLAN_SKELETON"]
