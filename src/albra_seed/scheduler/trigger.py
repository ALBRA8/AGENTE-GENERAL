"""Trigger types for the universal Scheduler.

Three concrete trigger types are supported:

  * OneShotTrigger   — fires once at a fixed datetime.
  * IntervalTrigger  — fires every `interval_seconds` from `start_at`.
  * CronTrigger      — fires on a simple cron-like expression
                       (lazy-imports `croniter` if installed; falls back
                       to a minimal parser supporting `*/N`, `N`, and
                       `*` fields for minute/hour/day-of-month/month/day).

All triggers expose:
  - next_run(after: datetime | None = None) -> datetime | None
  - is_one_shot -> bool
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Trigger(BaseModel):
    """Base trigger type."""

    def next_run(self, after: datetime | None = None) -> datetime | None:  # noqa: D401
        raise NotImplementedError

    @property
    def is_one_shot(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


# =====================================================================
# OneShotTrigger
# =====================================================================


class OneShotTrigger(Trigger):
    fire_at: datetime
    fired: bool = False

    @property
    def is_one_shot(self) -> bool:
        return True

    def next_run(self, after: datetime | None = None) -> datetime | None:
        if self.fired:
            return None
        target = _ensure_aware(self.fire_at)
        if after is not None:
            after = _ensure_aware(after)
            if after > target:
                return None
        return target

    def mark_fired(self) -> None:
        self.fired = True


# =====================================================================
# IntervalTrigger
# =====================================================================


class IntervalTrigger(Trigger):
    interval_seconds: float
    start_at: datetime = Field(default_factory=utc_now)
    end_at: datetime | None = None

    def next_run(self, after: datetime | None = None) -> datetime | None:
        anchor = _ensure_aware(self.start_at)
        if after is None:
            after = utc_now()
        after = _ensure_aware(after)
        # Find first anchor >= after
        if anchor >= after:
            candidate = anchor
        else:
            delta = after - anchor
            steps = int(delta.total_seconds() // self.interval_seconds) + 1
            candidate = anchor + timedelta(seconds=steps * self.interval_seconds)
        if self.end_at is not None and candidate > _ensure_aware(self.end_at):
            return None
        return candidate


# =====================================================================
# CronTrigger
# =====================================================================


class CronTrigger(Trigger):
    """Simple cron-like trigger.

    Supports 5-field cron expressions: `minute hour day-of-month month day-of-week`.
    Each field may be `*`, a single number, or `*/N`. Range lists
    (`1-5`) are NOT supported in the fallback parser — install
    `croniter` for the full syntax.

    If `croniter` is installed at construction time, the trigger uses it
    for `next_run`; otherwise it uses the minimal fallback parser.
    """

    expression: str
    timezone: str = "UTC"

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._use_croniter = False
        try:  # pragma: no cover
            from croniter import croniter  # type: ignore  # noqa: PLC0415

            self._croniter = croniter  # type: ignore[attr-defined]
            self._use_croniter = True
        except ImportError:
            self._croniter = None  # type: ignore[assignment]
            self._parsed = self._parse_fallback(self.expression)

    def next_run(self, after: datetime | None = None) -> datetime | None:
        if after is None:
            after = utc_now()
        after = _ensure_aware(after)
        if self._use_croniter:
            itr = self._croniter(self.expression, after)  # type: ignore[misc]
            return itr.get_next(datetime)  # type: ignore[no-any-return]
        return self._next_fallback(after)

    # ------------------------------------------------------------------
    # Minimal fallback parser: minute, hour, dom, month, dow.
    # ------------------------------------------------------------------
    def _parse_fallback(self, expr: str) -> dict[str, list[int]]:
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(
                f"cron expression must have 5 fields, got {len(parts)}: '{expr}'"
            )
        out: dict[str, list[int]] = {}
        bounds = {
            "minute": (0, 59),
            "hour": (0, 23),
            "dom": (1, 31),
            "month": (1, 12),
            "dow": (0, 6),
        }
        for field, raw in zip(bounds.keys(), parts):
            lo, hi = bounds[field]
            out[field] = self._expand_field(raw, lo, hi)
        return out

    @staticmethod
    def _expand_field(raw: str, lo: int, hi: int) -> list[int]:
        if raw == "*":
            return list(range(lo, hi + 1))
        if raw.startswith("*/"):
            step = int(raw[2:])
            return list(range(lo, hi + 1, step))
        # comma list?
        if "," in raw:
            vals: list[int] = []
            for part in raw.split(","):
                vals.extend(CronTrigger._expand_field(part, lo, hi))
            return sorted(set(vals))
        # single number
        val = int(raw)
        if not (lo <= val <= hi):
            raise ValueError(f"value {val} out of bounds [{lo}, {hi}]")
        return [val]

    def _next_fallback(self, after: datetime) -> datetime:
        p = self._parsed
        candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        # Brute-force scan for the next matching minute (bounded).
        for _ in range(60 * 24 * 366):
            if (
                candidate.minute in p["minute"]
                and candidate.hour in p["hour"]
                and candidate.day in p["dom"]
                and candidate.month in p["month"]
                and candidate.weekday() % 7 in p["dow"]
            ):
                return candidate
            candidate += timedelta(minutes=1)
        raise RuntimeError("no matching cron time found within a year")


__all__ = ["Trigger", "OneShotTrigger", "IntervalTrigger", "CronTrigger"]
