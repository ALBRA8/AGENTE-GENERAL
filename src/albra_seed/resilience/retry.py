"""RetryPolicy + RetryExecutor — exponential backoff with jitter.

Configurable:
  - max_retries      (attempts beyond the first one)
  - base_delay       (initial sleep before retry 1)
  - max_delay        (caps exponential growth)
  - jitter_fraction  (0..1) — adds uniform noise to damp thundering herds
  - retry_on          (list of error class names or substrings; empty = any)
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class RetryPolicy(BaseModel):
    """Pydantic model describing a retry policy."""

    max_retries: int = 3
    base_delay_seconds: float = 0.1
    max_delay_seconds: float = 5.0
    jitter_fraction: float = 0.25
    retry_on: list[str] = Field(default_factory=list)

    def should_retry(self, error: BaseException, attempt: int) -> bool:
        if attempt > self.max_retries:
            return False
        if not self.retry_on:
            return True
        name = type(error).__name__
        msg = str(error)
        for pattern in self.retry_on:
            if pattern == name or pattern in msg:
                return True
        return False

    def compute_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter. `attempt` is 1-based for the
        upcoming retry."""
        raw = self.base_delay_seconds * (2 ** (attempt - 1))
        raw = min(raw, self.max_delay_seconds)
        if self.jitter_fraction > 0:
            spread = raw * self.jitter_fraction
            raw = raw + random.uniform(-spread, spread)
        return max(0.0, raw)


@dataclass
class RetryOutcome:
    """Outcome of a RetryExecutor.run call."""

    success: bool
    value: Any = None
    error: BaseException | None = None
    attempts: int = 0
    total_delay_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


class RetryExecutor:
    """Executes an async callable under a RetryPolicy."""

    def __init__(self, policy: RetryPolicy | None = None) -> None:
        self.policy: RetryPolicy = policy or RetryPolicy()

    async def run(self, fn: Callable[[], Awaitable[T]]) -> RetryOutcome:
        attempts = 0
        total_delay = 0.0
        errors: list[str] = []
        last_error: BaseException | None = None
        # attempt 0 = first try; subsequent attempts are retries.
        while attempts <= self.policy.max_retries:
            attempts += 1
            try:
                value = await fn()
                return RetryOutcome(
                    success=True,
                    value=value,
                    attempts=attempts,
                    total_delay_seconds=total_delay,
                    errors=errors,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                errors.append(f"attempt_{attempts}: {type(exc).__name__}: {exc}")
                if not self.policy.should_retry(exc, attempts):
                    break
                if attempts > self.policy.max_retries:
                    break
                delay = self.policy.compute_delay(attempts)
                total_delay += delay
                await asyncio.sleep(delay)
        return RetryOutcome(
            success=False,
            error=last_error,
            attempts=attempts,
            total_delay_seconds=total_delay,
            errors=errors,
        )

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Like `run` but raises on exhausted retries."""
        outcome = await self.run(fn)
        if not outcome.success:
            assert outcome.error is not None
            raise outcome.error
        return outcome.value  # type: ignore[return-value]


__all__ = ["RetryPolicy", "RetryExecutor", "RetryOutcome"]
