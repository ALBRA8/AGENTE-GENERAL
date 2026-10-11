"""Autonomy levels (L0..L5) + AutonomyMatrix.

Re-exports `AutonomyLevel` from `albra_seed.core.types` (single source of
truth). Adds `AutonomyMatrix` — a mapping from (action_type, risk_level)
to required AutonomyLevel.

CRITICAL: Autonomy ≠ Permission.
  * Autonomy describes what the agent is ALLOWED to decide on its own.
  * Permissions gate concrete actions.
  * High autonomy does NOT bypass the firewall or policy engine.
  * Low autonomy does NOT grant permission.

Default AutonomyMatrix (the SEED ships this; specialization layers may
override):
    action_type     risk         required
    ─────────────── ──────────── ─────────────────
    observe         any          L0_OBSERVE
    recommend       any          L1_RECOMMEND
    execute_safe    LOW/MEDIUM   L2_EXECUTE_SAFE
    execute_safe    HIGH/CRIT    L3_EXECUTE_EXTERNAL
    execute_external LOW/MEDIUM  L3_EXECUTE_EXTERNAL
    execute_external HIGH/CRIT   L4_EXECUTE_SENSITIVE
    execute_sensitive any        L4_EXECUTE_SENSITIVE
    self_improve    any          L5_SELF_IMPROVE

DEFAULT-DENY: any unknown (action_type, risk_level) requires L5.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import AutonomyLevel, RiskLevel, new_id, utc_now
from datetime import datetime


# Re-export so callers can do `from albra_seed.autonomy.levels import AutonomyLevel`.
__all__ = ["AutonomyLevel", "AutonomyMatrix", "ActionType"]


# Action-type namespace (string, not enum — specialization layers may extend).
class ActionType:
    """Canonical action-type identifiers used by the default matrix.

    These are STRINGS — specialization layers may define additional
    action types without modifying the seed. The seed only defines
    the universal ones.
    """

    OBSERVE = "observe"
    RECOMMEND = "recommend"
    EXECUTE_SAFE = "execute_safe"
    EXECUTE_EXTERNAL = "execute_external"
    EXECUTE_SENSITIVE = "execute_sensitive"
    SELF_IMPROVE = "self_improve"


# Default required autonomy by (action_type, risk_level) — see module docstring.
_DEFAULT_MATRIX: dict[tuple[str, RiskLevel], AutonomyLevel] = {
    (ActionType.OBSERVE, RiskLevel.LOW): AutonomyLevel.L0_OBSERVE,
    (ActionType.OBSERVE, RiskLevel.MEDIUM): AutonomyLevel.L0_OBSERVE,
    (ActionType.OBSERVE, RiskLevel.HIGH): AutonomyLevel.L0_OBSERVE,
    (ActionType.OBSERVE, RiskLevel.CRITICAL): AutonomyLevel.L0_OBSERVE,
    (ActionType.OBSERVE, RiskLevel.UNKNOWN): AutonomyLevel.L0_OBSERVE,
    (ActionType.RECOMMEND, RiskLevel.LOW): AutonomyLevel.L1_RECOMMEND,
    (ActionType.RECOMMEND, RiskLevel.MEDIUM): AutonomyLevel.L1_RECOMMEND,
    (ActionType.RECOMMEND, RiskLevel.HIGH): AutonomyLevel.L1_RECOMMEND,
    (ActionType.RECOMMEND, RiskLevel.CRITICAL): AutonomyLevel.L1_RECOMMEND,
    (ActionType.RECOMMEND, RiskLevel.UNKNOWN): AutonomyLevel.L1_RECOMMEND,
    (ActionType.EXECUTE_SAFE, RiskLevel.LOW): AutonomyLevel.L2_EXECUTE_SAFE,
    (ActionType.EXECUTE_SAFE, RiskLevel.MEDIUM): AutonomyLevel.L2_EXECUTE_SAFE,
    (ActionType.EXECUTE_SAFE, RiskLevel.HIGH): AutonomyLevel.L3_EXECUTE_EXTERNAL,
    (ActionType.EXECUTE_SAFE, RiskLevel.CRITICAL): AutonomyLevel.L3_EXECUTE_EXTERNAL,
    (ActionType.EXECUTE_SAFE, RiskLevel.UNKNOWN): AutonomyLevel.L3_EXECUTE_EXTERNAL,
    (ActionType.EXECUTE_EXTERNAL, RiskLevel.LOW): AutonomyLevel.L3_EXECUTE_EXTERNAL,
    (ActionType.EXECUTE_EXTERNAL, RiskLevel.MEDIUM): AutonomyLevel.L3_EXECUTE_EXTERNAL,
    (ActionType.EXECUTE_EXTERNAL, RiskLevel.HIGH): AutonomyLevel.L4_EXECUTE_SENSITIVE,
    (ActionType.EXECUTE_EXTERNAL, RiskLevel.CRITICAL): AutonomyLevel.L4_EXECUTE_SENSITIVE,
    (ActionType.EXECUTE_EXTERNAL, RiskLevel.UNKNOWN): AutonomyLevel.L4_EXECUTE_SENSITIVE,
    (ActionType.EXECUTE_SENSITIVE, RiskLevel.LOW): AutonomyLevel.L4_EXECUTE_SENSITIVE,
    (ActionType.EXECUTE_SENSITIVE, RiskLevel.MEDIUM): AutonomyLevel.L4_EXECUTE_SENSITIVE,
    (ActionType.EXECUTE_SENSITIVE, RiskLevel.HIGH): AutonomyLevel.L4_EXECUTE_SENSITIVE,
    (ActionType.EXECUTE_SENSITIVE, RiskLevel.CRITICAL): AutonomyLevel.L4_EXECUTE_SENSITIVE,
    (ActionType.EXECUTE_SENSITIVE, RiskLevel.UNKNOWN): AutonomyLevel.L4_EXECUTE_SENSITIVE,
    (ActionType.SELF_IMPROVE, RiskLevel.LOW): AutonomyLevel.L5_SELF_IMPROVE,
    (ActionType.SELF_IMPROVE, RiskLevel.MEDIUM): AutonomyLevel.L5_SELF_IMPROVE,
    (ActionType.SELF_IMPROVE, RiskLevel.HIGH): AutonomyLevel.L5_SELF_IMPROVE,
    (ActionType.SELF_IMPROVE, RiskLevel.CRITICAL): AutonomyLevel.L5_SELF_IMPROVE,
    (ActionType.SELF_IMPROVE, RiskLevel.UNKNOWN): AutonomyLevel.L5_SELF_IMPROVE,
}


class AutonomyMatrix:
    """Maps (action_type, risk_level) → required AutonomyLevel.

    DEFAULT-DENY: unknown combinations require L5_SELF_IMPROVE.
    Specialization layers may override the default matrix.
    """

    def __init__(
        self,
        overrides: dict[tuple[str, RiskLevel], AutonomyLevel] | None = None,
        *,
        default_required: AutonomyLevel = AutonomyLevel.L5_SELF_IMPROVE,
    ) -> None:
        # Start with the seed defaults.
        self._matrix: dict[tuple[str, RiskLevel], AutonomyLevel] = dict(_DEFAULT_MATRIX)
        self._default_required = default_required
        if overrides:
            for key, level in overrides.items():
                self._matrix[key] = level

    def required_level(
        self,
        action_type: str,
        risk: RiskLevel = RiskLevel.UNKNOWN,
    ) -> AutonomyLevel:
        """Return the AutonomyLevel required for (action_type, risk).

        DEFAULT-DENY: returns `_default_required` (L5 by default) for
        unknown combinations.
        """
        return self._matrix.get((action_type, risk), self._default_required)

    def set(
        self,
        action_type: str,
        risk: RiskLevel,
        required: AutonomyLevel,
    ) -> None:
        self._matrix[(action_type, risk)] = required

    def as_dict(self) -> dict[tuple[str, RiskLevel], AutonomyLevel]:
        return dict(self._matrix)

    def known_action_types(self) -> set[str]:
        return {at for (at, _) in self._matrix.keys()}


__all__ = ["AutonomyLevel", "AutonomyMatrix", "ActionType"]
