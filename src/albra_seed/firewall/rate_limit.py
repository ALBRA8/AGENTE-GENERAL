"""RateLimiter — token-bucket per (actor, action).

A token-bucket refills at a steady rate up to a maximum capacity.
Every action consumes one token. If no tokens are available, the
limiter reports "throttled" and the firewall should DENY.

Bucket key: (actor_id, action_name). Each bucket is independent.

Configuration:
  * capacity       — max tokens in the bucket (burst size)
  * refill_per_sec — tokens added per second (sustained rate)
  * initial_tokens — defaults to `capacity`

Thread-unsafe by design (single-loop async). For multi-process, back
with Redis or a shared store.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger("albra.firewall.rate_limit")


@dataclass
class _Bucket:
    tokens: float
    last_refill: float = field(default_factory=time.time)

    def refill(self, capacity: float, refill_per_sec: float, *, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        elapsed = max(0.0, now - self.last_refill)
        self.tokens = min(capacity, self.tokens + elapsed * refill_per_sec)
        self.last_refill = now

    def consume(self, amount: float = 1.0) -> bool:
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False


class RateLimiter:
    """Token-bucket rate limiter keyed by (actor_id, action_name).

    DEFAULT-DENY semantics: if no tokens are available, `try_consume`
    returns False and the caller MUST deny the action.
    """

    def __init__(
        self,
        *,
        capacity: float = 10.0,
        refill_per_sec: float = 1.0,
        initial_tokens: float | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if refill_per_sec <= 0:
            raise ValueError("refill_per_sec must be > 0")
        self._capacity = float(capacity)
        self._refill_per_sec = float(refill_per_sec)
        self._initial = (
            float(initial_tokens) if initial_tokens is not None else float(capacity)
        )
        self._buckets: dict[tuple[str, str], _Bucket] = {}

    def _key(self, actor_id: str, action_name: str) -> tuple[str, str]:
        return (actor_id, action_name)

    def _get_or_create(self, actor_id: str, action_name: str) -> _Bucket:
        key = self._key(actor_id, action_name)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self._initial)
            self._buckets[key] = bucket
        return bucket

    def try_consume(
        self,
        actor_id: str,
        action_name: str,
        *,
        amount: float = 1.0,
        now: float | None = None,
    ) -> bool:
        """Consume `amount` tokens. Returns True if allowed, False if throttled."""
        bucket = self._get_or_create(actor_id, action_name)
        bucket.refill(self._capacity, self._refill_per_sec, now=now)
        allowed = bucket.consume(amount)
        if not allowed:
            log.info(
                "rate_limit.throttled",
                actor=actor_id,
                action=action_name,
                tokens_remaining=bucket.tokens,
            )
        return allowed

    def tokens_available(
        self,
        actor_id: str,
        action_name: str,
        *,
        now: float | None = None,
    ) -> float:
        """Return the current token count without consuming."""
        bucket = self._get_or_create(actor_id, action_name)
        bucket.refill(self._capacity, self._refill_per_sec, now=now)
        return bucket.tokens

    def reset(self, actor_id: str, action_name: str) -> None:
        self._buckets.pop(self._key(actor_id, action_name), None)

    def reset_all(self) -> None:
        self._buckets.clear()

    @property
    def capacity(self) -> float:
        return self._capacity

    @property
    def refill_per_sec(self) -> float:
        return self._refill_per_sec


__all__ = ["RateLimiter"]
