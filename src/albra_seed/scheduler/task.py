"""ScheduledTask model.

A ScheduledTask binds a Trigger to a target (workflow/job/skill name)
plus a payload. It tracks `enabled`, `last_run`, and `next_run`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now
from albra_seed.scheduler.trigger import (
    CronTrigger,
    IntervalTrigger,
    OneShotTrigger,
    Trigger,
)


class ScheduledTask(BaseModel):
    """A scheduled future task."""

    id: str = Field(default_factory=lambda: new_id("sched"))
    name: str = ""
    target: str  # workflow/job/skill name
    target_type: str = "job"  # job | workflow | skill
    payload: dict[str, Any] = Field(default_factory=dict)
    trigger: Trigger
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    run_count: int = 0

    def recompute_next_run(self, *, after: datetime | None = None) -> None:
        if not self.enabled:
            self.next_run = None
            return
        self.next_run = self.trigger.next_run(after=after)

    def mark_run(self, *, when: datetime | None = None) -> None:
        self.last_run = when or utc_now()
        self.run_count += 1
        if isinstance(self.trigger, OneShotTrigger):
            self.trigger.mark_fired()
            self.next_run = None
        else:
            self.recompute_next_run(after=self.last_run)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = ["ScheduledTask"]
