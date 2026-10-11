"""Default reasoning engine.

Uses the `LLMProvider` (via the provider router) when available for
decomposition. Falls back to a deterministic rule-based decomposition
when no provider is configured (CORE_ONLY mode) — the rule-based path
delegates to the `Planner` class which uses the canonical skeleton.

The engine contains NO domain knowledge.
"""

from __future__ import annotations

import json
from typing import Any

from albra_seed.core.base import LLMProvider
from albra_seed.core.types import Message, Role
from albra_seed.reasoning.engine import (
    DecomposedStep,
    Decomposition,
    Plan,
    PlanStep,
    PriorityItem,
    Prioritization,
    ReasoningEngine,
    ValidationResult,
)
from albra_seed.reasoning.planner import Planner
from albra_seed.reasoning.prompts import PromptManager


class DefaultReasoningEngine(ReasoningEngine):
    """Generic reasoning engine with optional LLM-backed decomposition."""

    name: str = "default"

    def __init__(
        self,
        *,
        provider: LLMProvider | None = None,
        planner: Planner | None = None,
        prompts: PromptManager | None = None,
    ) -> None:
        self._provider: LLMProvider | None = provider
        self._planner: Planner = planner or Planner()
        self._prompts: PromptManager = prompts or PromptManager()

    # ------------------------------------------------------------------ #
    # decompose                                                           #
    # ------------------------------------------------------------------ #

    async def decompose(self, task: str) -> Decomposition:
        """Decompose a task. Uses LLM if available; otherwise rule-based."""
        if self._provider is None:
            return await self._planner.decompose(task)
        try:
            decomp = await self._llm_decompose(task)
            if decomp is not None:
                return decomp
        except Exception:
            # Never let a provider error kill reasoning — fall back.
            pass
        return await self._planner.decompose(task)

    async def _llm_decompose(self, task: str) -> Decomposition | None:
        if self._provider is None:
            return None
        prompt = self._prompts.render("task_decomposition", {"task": task})
        messages = [
            Message(role=Role.SYSTEM, content="You decompose tasks into steps."),
            Message(role=Role.USER, content=prompt),
        ]
        response = await self._provider.complete(messages)
        if not response.content:
            return None
        steps = self._parse_steps(response.content)
        if not steps:
            return None
        return Decomposition(task=task, steps=steps, metadata={"source": "llm"})

    @staticmethod
    def _parse_steps(content: str) -> list[DecomposedStep]:
        """Best-effort parse of LLM output into DecomposedStep objects."""
        # Strip code fences if present.
        text = content.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            data = json.loads(text)
        except Exception:
            return []
        steps: list[DecomposedStep] = []
        for idx, item in enumerate(data.get("steps", [])):
            steps.append(
                DecomposedStep(
                    title=item.get("title", f"step-{idx}"),
                    description=item.get("description", ""),
                    depends_on=list(item.get("depends_on", [])),
                    order=idx,
                )
            )
        return steps

    # ------------------------------------------------------------------ #
    # plan                                                                #
    # ------------------------------------------------------------------ #

    async def plan(self, decomposition: Decomposition) -> Plan:
        """Convert a decomposition into a plan via the Planner."""
        return await self._planner.plan(
            decomposition.task,
            decomposition=decomposition,
        )

    # ------------------------------------------------------------------ #
    # prioritize                                                          #
    # ------------------------------------------------------------------ #

    async def prioritize(self, items: list[PriorityItem]) -> Prioritization:
        """Order items by descending weight (deterministic tiebreak by id)."""
        ordered = sorted(
            items,
            key=lambda it: (-it.weight, it.item_id),
        )
        return Prioritization(
            ordering=[it.item_id for it in ordered],
            rationale="descending weight; id tiebreak",
        )

    # ------------------------------------------------------------------ #
    # validate                                                            #
    # ------------------------------------------------------------------ #

    async def validate(self, result: Any) -> ValidationResult:
        """Generic structural validation of a result.

        Rules (no domain knowledge):
        - Non-empty strings pass.
        - Pydantic BaseModel instances pass.
        - dicts pass if non-empty.
        - None / empty fail with a clear error.
        """
        errors: list[str] = []
        warnings: list[str] = []
        is_valid = True
        if result is None:
            return ValidationResult(is_valid=False, errors=["result is None"])
        if isinstance(result, str) and not result.strip():
            return ValidationResult(is_valid=False, errors=["result is empty string"])
        if isinstance(result, (list, dict)) and len(result) == 0:
            return ValidationResult(
                is_valid=False,
                errors=[f"result of type {type(result).__name__} is empty"],
            )
        if isinstance(result, str):
            warnings.append("result is a plain string; consider a structured model")
        return ValidationResult(
            is_valid=is_valid,
            confidence=0.9 if not warnings else 0.7,
            warnings=warnings,
            metadata={"type": type(result).__name__},
        )


__all__ = ["DefaultReasoningEngine"]
