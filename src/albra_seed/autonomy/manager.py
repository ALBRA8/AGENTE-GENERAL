"""AutonomyManager — checks if an agent's autonomy_level is sufficient.

`can_perform(agent_autonomy, action_required) -> bool` returns True if
the agent's autonomy_level is >= the action's required level.

CRITICAL: Autonomy ≠ Permission. Passing this check does NOT grant
permission. The PolicyEngine composes BOTH checks (autonomy AND
permission) before issuing an ALLOW.

DEFAULT-DENY: any comparison involving an unknown required level
returns False (the action must be escalated to HITL).
"""

from __future__ import annotations

from albra_seed.autonomy.levels import AutonomyLevel, AutonomyMatrix
from albra_seed.core.types import RiskLevel


class AutonomyManager:
    """Verifies that an agent's autonomy_level meets an action's requirement.

    Constructed with an `AutonomyMatrix` (defaults to the seed's matrix)
    and an optional default required level for unknown action types.
    """

    def __init__(
        self,
        matrix: AutonomyMatrix | None = None,
    ) -> None:
        self.matrix: AutonomyMatrix = matrix or AutonomyMatrix()

    def can_perform(
        self,
        agent_autonomy: AutonomyLevel,
        action_required: AutonomyLevel,
    ) -> bool:
        """True iff `agent_autonomy` >= `action_required`.

        Uses the int values of the enum (L0=0 ... L5=5).
        """
        return int(agent_autonomy) >= int(action_required)

    def can_perform_action(
        self,
        agent_autonomy: AutonomyLevel,
        action_type: str,
        risk: RiskLevel = RiskLevel.UNKNOWN,
    ) -> bool:
        """True iff the agent's autonomy is sufficient for the action
        type + risk, per the matrix."""
        required = self.matrix.required_level(action_type, risk)
        return self.can_perform(agent_autonomy, required)

    def required_for(
        self,
        action_type: str,
        risk: RiskLevel = RiskLevel.UNKNOWN,
    ) -> AutonomyLevel:
        return self.matrix.required_level(action_type, risk)


__all__ = ["AutonomyManager"]
