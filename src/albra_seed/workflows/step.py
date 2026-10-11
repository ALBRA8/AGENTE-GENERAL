"""WorkflowStep — a single node in a WorkflowGraph.

A step has an id, name, a type (action/tool/skill/decision/handoff/wait),
inputs/outputs declared as free-form dicts, dependencies (other step ids
that must run first), an optional retry policy, a timeout, and flags for
HITL (Human-In-The-Loop) and evidence requirements.

Steps are pydantic v2 models so they serialize cleanly for persistence.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import RiskLevel


class WorkflowStepType(str, Enum):
    ACTION = "action"
    TOOL = "tool"
    SKILL = "skill"
    DECISION = "decision"
    HANDOFF = "handoff"
    WAIT = "wait"
    HITL = "hitl"  # interrupt + await human input


class RetryPolicy(BaseModel):
    """Per-step retry policy (composed with the resilience subpackage)."""

    max_retries: int = 0
    base_delay_seconds: float = 0.1
    max_delay_seconds: float = 5.0
    retry_on: list[str] = Field(default_factory=list)  # error substrings; empty = any


class WorkflowStep(BaseModel):
    """A single step in a workflow definition."""

    id: str
    name: str = ""
    type: WorkflowStepType = WorkflowStepType.ACTION
    description: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)

    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: float = 30.0

    hitl_required: bool = False
    evidence_required: bool = False

    risk_level: RiskLevel = RiskLevel.LOW
    permissions: list[str] = Field(default_factory=list)

    # Branching: if non-empty, defines a list of (condition, target_step_id)
    # tuples. Evaluated after the step succeeds; first match wins. If none
    # match, the workflow continues to default successors.
    branches: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def is_decision(self) -> bool:
        return self.type == WorkflowStepType.DECISION or bool(self.branches)


__all__ = ["WorkflowStep", "WorkflowStepType", "RetryPolicy"]
