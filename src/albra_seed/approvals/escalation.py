"""EscalationPolicy — rules for escalating stale approvals.

An EscalationPolicy declares:
  * stale_after_seconds — if a PENDING approval has been waiting
    longer than this, it becomes eligible for escalation.
  * escalate_to         — the next-level approver (string id; the seed
    does NOT impose an org chart — specialization layers define this).
  * max_escalations     — hard cap on the length of the escalation
    chain (DEFAULT 3). After this, the request auto-EXPIRES.
  * on_stale            — one of: escalate | expire | reject

The seed ships a single conservative default policy: escalate after
30 minutes, max 3 escalations, expire if exceeded. Specialization layers
override per-domain.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now


class EscalationPolicy(BaseModel):
    """Rules for escalating stale HITL approvals."""

    stale_after_seconds: float = 1800.0  # 30 min
    escalate_to: str = "supervisor"
    max_escalations: int = 3
    on_stale: str = "escalate"  # escalate | expire | reject
    next_deadline_extension_seconds: float = 1800.0  # extended deadline after escalation
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_stale(
        self,
        requested_at: datetime,
        *,
        now: datetime | None = None,
    ) -> bool:
        now = now or utc_now()
        age = (now - requested_at).total_seconds()
        return age >= self.stale_after_seconds

    def can_escalate(self, escalation_chain: list[str]) -> bool:
        return len(escalation_chain) < self.max_escalations

    def next_deadline(
        self,
        *,
        now: datetime | None = None,
    ) -> datetime:
        now = now or utc_now()
        return now + timedelta(seconds=self.next_deadline_extension_seconds)


__all__ = ["EscalationPolicy"]
