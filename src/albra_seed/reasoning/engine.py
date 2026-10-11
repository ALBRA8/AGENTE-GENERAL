"""Reasoning engine — abstract base + structured outputs.

The reasoning engine decomposes tasks, plans steps, prioritizes
items, and validates results. It contains ZERO domain knowledge:
the decomposition strategy, planning template, and validation rules
are all generic. Specialization layers register domain-specific
ReasoningEngines if they need richer behaviour.

All methods are async and return pydantic models so callers can
introspect the structure deterministically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now


class DecomposedStep(BaseModel):
    """A single decomposed sub-task."""

    step_id: str = Field(default_factory=lambda: new_id("step"))
    title: str
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)
    order: int = 0


class Decomposition(BaseModel):
    """The output of `decompose(task)`."""

    task: str
    steps: list[DecomposedStep] = Field(default_factory=list)
    created_at: Any = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    """A single plan step."""

    step_id: str = Field(default_factory=lambda: new_id("pstep"))
    title: str
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)
    order: int = 0
    expected_output: str = ""
    is_optional: bool = False


class Plan(BaseModel):
    """The output of `plan(steps)` — an executable, dependency-ordered plan."""

    plan_id: str = Field(default_factory=lambda: new_id("plan"))
    steps: list[PlanStep] = Field(default_factory=list)
    created_at: Any = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PriorityItem(BaseModel):
    """An item passed to `prioritize(items)`."""

    item_id: str
    title: str
    weight: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Prioritization(BaseModel):
    """The output of `prioritize(items)`."""

    ordering: list[str] = Field(default_factory=list)  # item_ids in priority order
    rationale: str = ""
    created_at: Any = Field(default_factory=utc_now)


class ValidationResult(BaseModel):
    """The output of `validate(result)`."""

    is_valid: bool
    confidence: float = 0.0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReasoningEngine(ABC):
    """Abstract base for reasoning engines.

    Specialization layers may swap in a domain-aware implementation;
    the SEED ships only the generic DefaultReasoningEngine.
    """

    name: str = "base"

    @abstractmethod
    async def decompose(self, task: str) -> Decomposition:
        """Break a task into ordered sub-steps."""
        raise NotImplementedError

    @abstractmethod
    async def plan(self, decomposition: Decomposition) -> Plan:
        """Convert a decomposition into an executable Plan."""
        raise NotImplementedError

    @abstractmethod
    async def prioritize(self, items: list[PriorityItem]) -> Prioritization:
        """Sort items by priority."""
        raise NotImplementedError

    @abstractmethod
    async def validate(self, result: Any) -> ValidationResult:
        """Validate a result against the engine's rules."""
        raise NotImplementedError


__all__ = [
    "ReasoningEngine",
    "DecomposedStep",
    "Decomposition",
    "PlanStep",
    "Plan",
    "PriorityItem",
    "Prioritization",
    "ValidationResult",
]
