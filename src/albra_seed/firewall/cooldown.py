"""CooldownManager — per-action cooldowns.

A cooldown is a temporal pause applied to an action after it has been
performed (or attempted). While an action is cooling down, the firewall
returns DENY for it.

Use cases:
  - Rate-limit retry storms on flaky external tools.
  - Enforce backoff after a dangerous action.
  - Cool down an action after a kill-switch deactivation.

Methods:
  trigger(action)              — start a cooldown for `action` (default duration).
  trigger(action, seconds)     — start a cooldown for `seconds`.
  is_cooling_down(action)      — True if currently cooling down.
  remaining(action) -> int     — seconds remaining (0 if not cooling).
  clear(action)                — clear the cooldown for `action`.
  clear_all()                  — clear all cooldowns.
"""

from __future__ import annotations

import time
from typing import Any

import structlog
from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now
from datetime import datetime

log = structlog.get_logger("albra.firewall.cooldown")


class CooldownRecord(BaseModel):
    """A single active cooldown."""

    record_id: str = Field(default_factory=lambda: new_id("cd"))
    action: str
    triggered_at: float = Field(default_factory=time.time)  # epoch seconds
    duration_seconds: float
    reason: str = ""
    triggered_by: str = "system"

    def expires_at(self) -> float:
        return self.triggered_at + self.duration_seconds

    def remaining(self, *, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        return max(0.0, self.expires_at() - now)


class CooldownManager:
    """Per-action cooldown manager. Thread-unsafe by design (single-loop async).

    For multi-process scenarios, back this with a shared store (Redis).
    The seed ships only the in-memory implementation.
    """

    def __init__(self, default_duration_seconds: float = 60.0) -> None:
        self._default = default_duration_seconds
        self._cooldowns: dict[str, CooldownRecord] = {}

    def trigger(
        self,
        action: str,
        *,
        seconds: float | None = None,
        reason: str = "",
        triggered_by: str = "system",
    ) -> CooldownRecord:
        duration = seconds if seconds is not None else self._default
        rec = CooldownRecord(
            action=action,
            duration_seconds=duration,
            reason=reason,
            triggered_by=triggered_by,
        )
        self._cooldowns[action] = rec
        log.info(
            "cooldown.trigger",
            action=action,
            duration=duration,
            reason=reason,
            triggered_by=triggered_by,
        )
        return rec

    def is_cooling_down(self, action: str, *, now: float | None = None) -> bool:
        rec = self._cooldowns.get(action)
        if rec is None:
            return False
        if rec.remaining(now=now) <= 0:
            # Expired — garbage-collect.
            del self._cooldowns[action]
            return False
        return True

    def remaining(self, action: str, *, now: float | None = None) -> int:
        rec = self._cooldowns.get(action)
        if rec is None:
            return 0
        rem = rec.remaining(now=now)
        if rem <= 0:
            del self._cooldowns[action]
            return 0
        return int(rem)

    def clear(self, action: str) -> bool:
        return self._cooldowns.pop(action, None) is not None

    def clear_all(self) -> int:
        n = len(self._cooldowns)
        self._cooldowns.clear()
        return n

    def active(self) -> list[CooldownRecord]:
        """Return all currently-active cooldown records (post-GC)."""
        now = time.time()
        expired = [a for a, r in self._cooldowns.items() if r.remaining(now=now) <= 0]
        for a in expired:
            del self._cooldowns[a]
        return list(self._cooldowns.values())


__all__ = ["CooldownManager", "CooldownRecord"]
