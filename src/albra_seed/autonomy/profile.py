"""AutonomyProfile — per-agent autonomy configuration.

An AutonomyProfile bundles an agent's autonomy_level with policy
constraints: max risk they can run autonomously, max budget (cost /
tokens), max duration per action, allowed action types, etc.

This is a *configuration* record, not a runtime state. It is meant to
be loaded at agent construction time and consulted by the policy
engine + firewall when evaluating actions.

CRITICAL: Autonomy ≠ Permission. A profile with L5 autonomy still
requires permissions per concrete action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import (
    AutonomyLevel,
    RiskLevel,
    new_id,
    utc_now,
)


class AutonomyProfile(BaseModel):
    """Per-agent autonomy configuration.

    The `agent_id` binds this profile to a specific agent. Multiple
    agents may share the same profile_id (template). Specialization
    layers create profile templates; the seed ships none.
    """

    profile_id: str = Field(default_factory=lambda: new_id("autprof"))
    agent_id: str
    autonomy_level: AutonomyLevel = AutonomyLevel.L0_OBSERVE
    max_autonomous_risk: RiskLevel = RiskLevel.LOW
    allowed_action_types: list[str] = Field(default_factory=list)
    forbidden_action_types: list[str] = Field(default_factory=list)
    max_budget_per_action: float | None = None  # currency-neutral units
    max_duration_seconds: int | None = None
    requires_supervision: bool = False
    supervision_channel: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime | None = None

    def allows_action_type(self, action_type: str) -> bool:
        """True if action_type is allowed (not in forbidden; in allowed
        if allowed list is non-empty)."""
        if action_type in self.forbidden_action_types:
            return False
        if self.allowed_action_types and action_type not in self.allowed_action_types:
            return False
        return True

    def can_run_risk(self, risk: RiskLevel) -> bool:
        """True if the risk level is at or below the profile's max."""
        order = [RiskLevel.UNKNOWN, RiskLevel.LOW, RiskLevel.MEDIUM,
                 RiskLevel.HIGH, RiskLevel.CRITICAL]
        try:
            return order.index(risk) <= order.index(self.max_autonomous_risk)
        except ValueError:
            return False

    def touch(self) -> None:
        self.updated_at = utc_now()


__all__ = ["AutonomyProfile"]
