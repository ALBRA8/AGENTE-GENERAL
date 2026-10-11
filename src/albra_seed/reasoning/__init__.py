"""Reasoning subpackage — generic, non-domain reasoning engine.

Public API:
    from albra_seed.reasoning import (
        ReasoningEngine,
        DefaultReasoningEngine,
        Planner,
        Plan, PlanStep,
        Decomposition, DecomposedStep,
        PriorityItem, Prioritization,
        ValidationResult,
        PromptManager,
        BUILTIN_TEMPLATES,
    )
"""

from __future__ import annotations

from albra_seed.reasoning.default import DefaultReasoningEngine
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
from albra_seed.reasoning.planner import CANONICAL_PLAN_SKELETON, Planner
from albra_seed.reasoning.prompts import BUILTIN_TEMPLATES, PromptManager

__all__ = [
    "ReasoningEngine",
    "DefaultReasoningEngine",
    "Planner",
    "Plan",
    "PlanStep",
    "Decomposition",
    "DecomposedStep",
    "PriorityItem",
    "Prioritization",
    "ValidationResult",
    "PromptManager",
    "BUILTIN_TEMPLATES",
    "CANONICAL_PLAN_SKELETON",
]
