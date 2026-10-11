"""SkillLifecycle — tracks installation / activation / version transitions.

A `Skill` may go through several states during its lifetime:
  PENDING (just registered)
  ACTIVE   (validated + enabled)
  INACTIVE (disabled, but still registered)
  DEPRECATED (about to be removed)
  REMOVED  (uninstalled)

SkillLifecycle records every transition with timestamp + reason so the
audit log can replay the history of any skill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from albra_seed.core.types import utc_now


class SkillState(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


@dataclass
class LifecycleEvent:
    skill_id: str
    from_state: SkillState | None
    to_state: SkillState
    reason: str = ""
    timestamp: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "from_state": self.from_state.value if self.from_state else None,
            "to_state": self.to_state.value,
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
            "metadata": dict(self.metadata),
        }


_VALID_TRANSITIONS: dict[SkillState, set[SkillState]] = {
    SkillState.PENDING: {SkillState.ACTIVE, SkillState.INACTIVE, SkillState.REMOVED},
    SkillState.ACTIVE: {SkillState.INACTIVE, SkillState.DEPRECATED, SkillState.REMOVED},
    SkillState.INACTIVE: {SkillState.ACTIVE, SkillState.DEPRECATED, SkillState.REMOVED},
    SkillState.DEPRECATED: {SkillState.REMOVED, SkillState.INACTIVE},
    SkillState.REMOVED: set(),
}


class InvalidTransitionError(ValueError):
    pass


class SkillLifecycle:
    """Tracks the lifecycle of skills by id."""

    def __init__(self) -> None:
        self._states: dict[str, SkillState] = {}
        self._versions: dict[str, str] = {}
        self._history: list[LifecycleEvent] = []

    # ------------------------------------------------------------------
    def install(self, skill_id: str, version: str = "1.0.0") -> SkillState:
        if skill_id in self._states:
            raise ValueError(f"skill '{skill_id}' already installed")
        self._states[skill_id] = SkillState.PENDING
        self._versions[skill_id] = version
        self._history.append(
            LifecycleEvent(
                skill_id=skill_id,
                from_state=None,
                to_state=SkillState.PENDING,
                reason="installed",
            )
        )
        return SkillState.PENDING

    def activate(self, skill_id: str, *, reason: str = "activated") -> SkillState:
        return self._transition(skill_id, SkillState.ACTIVE, reason=reason)

    def deactivate(self, skill_id: str, *, reason: str = "deactivated") -> SkillState:
        return self._transition(skill_id, SkillState.INACTIVE, reason=reason)

    def deprecate(self, skill_id: str, *, reason: str = "deprecated") -> SkillState:
        return self._transition(skill_id, SkillState.DEPRECATED, reason=reason)

    def remove(self, skill_id: str, *, reason: str = "removed") -> SkillState:
        return self._transition(skill_id, SkillState.REMOVED, reason=reason)

    def upgrade_version(self, skill_id: str, new_version: str) -> str:
        if skill_id not in self._states:
            raise ValueError(f"skill '{skill_id}' not installed")
        old = self._versions[skill_id]
        self._versions[skill_id] = new_version
        self._history.append(
            LifecycleEvent(
                skill_id=skill_id,
                from_state=self._states[skill_id],
                to_state=self._states[skill_id],
                reason=f"version upgrade {old} -> {new_version}",
                metadata={"old_version": old, "new_version": new_version},
            )
        )
        return new_version

    # ------------------------------------------------------------------
    def state(self, skill_id: str) -> SkillState | None:
        return self._states.get(skill_id)

    def version(self, skill_id: str) -> str | None:
        return self._versions.get(skill_id)

    def is_active(self, skill_id: str) -> bool:
        return self._states.get(skill_id) == SkillState.ACTIVE

    def history(self, skill_id: str | None = None) -> list[LifecycleEvent]:
        if skill_id is None:
            return list(self._history)
        return [e for e in self._history if e.skill_id == skill_id]

    def all_states(self) -> dict[str, str]:
        return {sid: s.value for sid, s in self._states.items()}

    # ------------------------------------------------------------------
    def _transition(
        self,
        skill_id: str,
        to_state: SkillState,
        *,
        reason: str,
    ) -> SkillState:
        current = self._states.get(skill_id)
        if current is None:
            raise ValueError(f"skill '{skill_id}' not installed")
        allowed = _VALID_TRANSITIONS.get(current, set())
        if to_state not in allowed:
            raise InvalidTransitionError(
                f"invalid transition {current.value} -> {to_state.value} for skill '{skill_id}'"
            )
        self._states[skill_id] = to_state
        self._history.append(
            LifecycleEvent(
                skill_id=skill_id,
                from_state=current,
                to_state=to_state,
                reason=reason,
            )
        )
        return to_state
