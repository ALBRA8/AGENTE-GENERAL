"""Scheduler subpackage — universal Scheduler + ScheduledTask + Triggers.

Public API:
    Scheduler         — schedule / cancel / list / tick
    ScheduledTask     — model binding a Trigger to a target
    OneShotTrigger    — fire once at fixed datetime
    IntervalTrigger   — fire every N seconds
    CronTrigger       — cron-like expression (lazy croniter, fallback parser)
"""

from albra_seed.scheduler.scheduler import DispatchFn, Scheduler
from albra_seed.scheduler.task import ScheduledTask
from albra_seed.scheduler.trigger import (
    CronTrigger,
    IntervalTrigger,
    OneShotTrigger,
    Trigger,
)

__all__ = [
    "Scheduler",
    "ScheduledTask",
    "Trigger",
    "OneShotTrigger",
    "IntervalTrigger",
    "CronTrigger",
    "DispatchFn",
]
