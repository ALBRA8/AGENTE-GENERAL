"""Autonomy subpackage — autonomy levels (L0..L5) + matrix + manager + profile.

CRITICAL INVARIANTS:
  * Autonomy ≠ Permission. Autonomy describes what an agent may decide
    on its own. Permissions gate concrete actions.
  * L5 does NOT bypass the firewall or policy engine.
  * DEFAULT-DENY: unknown action types / risk levels require L5.
  * can_perform(agent, required) is a pure integer comparison; the
    PolicyEngine composes this with permission + rule checks.
"""

from albra_seed.autonomy.levels import ActionType, AutonomyLevel, AutonomyMatrix
from albra_seed.autonomy.manager import AutonomyManager
from albra_seed.autonomy.profile import AutonomyProfile

__all__ = [
    "AutonomyLevel",
    "AutonomyMatrix",
    "ActionType",
    "AutonomyManager",
    "AutonomyProfile",
]
