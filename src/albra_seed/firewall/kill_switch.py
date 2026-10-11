"""KillSwitch — global emergency stop.

When the kill switch is ACTIVE, EVERY external action is blocked
(returns True from `block(action)`). State is persisted with timestamp
+ reason in the audit log. A cooldown period is enforced after
deactivation to prevent immediate re-activation storms.

The kill switch is GLOBAL — it is not per-agent, not per-action, not
per-domain. Activating it halts the entire seed.

This is the SEED's single most powerful safety primitive. Use sparingly.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Awaitable

import structlog
from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now

log = structlog.get_logger("albra.firewall.kill_switch")

# Type alias for an async audit sink (audit_logger.append(record) -> None).
AuditSink = Callable[[dict[str, Any]], Awaitable[None]]


class KillSwitchState(BaseModel):
    """Snapshot of the kill switch state."""

    active: bool = False
    activated_at: datetime | None = None
    deactivated_at: datetime | None = None
    reason: str = ""
    activated_by: str = ""
    cooldown_until: float = 0.0  # epoch seconds; 0 = no cooldown
    activation_count: int = 0


class KillSwitch:
    """Global kill switch — blocks ALL external actions when active."""

    def __init__(
        self,
        *,
        cooldown_after_deactivate_seconds: float = 30.0,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._state = KillSwitchState()
        self._cooldown_after = cooldown_after_deactivate_seconds
        self._audit_sink: AuditSink | None = audit_sink

    # ------------------------------------------------------------------
    # State inspection
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        return self._state.active

    def state(self) -> KillSwitchState:
        return self._state.model_copy()

    def block(self, action: str | None = None) -> bool:
        """Returns True if the action MUST be blocked.

        If the kill switch is active, returns True for ALL actions
        (the `action` argument is for logging only).
        """
        if self._state.active:
            log.critical(
                "kill_switch.block",
                action=action or "<unknown>",
                reason=self._state.reason,
                activated_by=self._state.activated_by,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Activation / deactivation
    # ------------------------------------------------------------------

    async def activate(
        self,
        reason: str,
        *,
        activated_by: str = "system",
    ) -> KillSwitchState:
        """Activate the kill switch. Idempotent if already active.

        Records an audit event with timestamp + reason + actor.
        """
        if self._state.active:
            log.warning("kill_switch.already_active", reason=reason)
            return self._state
        self._state.active = True
        self._state.activated_at = utc_now()
        self._state.reason = reason
        self._state.activated_by = activated_by
        self._state.activation_count += 1
        log.critical(
            "kill_switch.activated",
            reason=reason,
            activated_by=activated_by,
            activation_count=self._state.activation_count,
        )
        await self._audit("kill_switch.activated", reason=reason, activated_by=activated_by)
        return self._state

    async def deactivate(self, *, by: str = "system") -> KillSwitchState:
        """Deactivate the kill switch. Enforces a cooldown period during
        which re-activation is forbidden (prevents flapping)."""
        if not self._state.active:
            return self._state
        self._state.active = False
        self._state.deactivated_at = utc_now()
        self._state.cooldown_until = time.time() + self._cooldown_after
        log.warning(
            "kill_switch.deactivated",
            by=by,
            cooldown_seconds=self._cooldown_after,
        )
        await self._audit("kill_switch.deactivated", by=by)
        return self._state

    def in_cooldown(self, *, now: float | None = None) -> bool:
        """True if the kill switch is currently in the post-deactivation
        cooldown window (re-activation forbidden)."""
        if self._state.cooldown_until <= 0:
            return False
        now = now if now is not None else time.time()
        return now < self._state.cooldown_until

    def cooldown_remaining(self, *, now: float | None = None) -> float:
        if not self.in_cooldown(now=now):
            return 0.0
        now = now if now is not None else time.time()
        return max(0.0, self._state.cooldown_until - now)

    # ------------------------------------------------------------------
    # Audit sink
    # ------------------------------------------------------------------

    def set_audit_sink(self, sink: AuditSink) -> None:
        self._audit_sink = sink

    async def _audit(self, event: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "event": event,
            "timestamp": utc_now().isoformat(),
            "activation_count": self._state.activation_count,
            **fields,
        }
        if self._audit_sink is not None:
            try:
                await self._audit_sink(record)
            except Exception as exc:  # noqa: BLE001
                # Audit failures must NEVER prevent the kill switch from
                # operating — but they MUST be logged.
                log.error(
                    "kill_switch.audit_failed",
                    error=str(exc),
                    audit_event=event,
                )
        else:
            log.info("kill_switch.audit_no_sink", audit_event=event)


__all__ = ["KillSwitch", "KillSwitchState"]
