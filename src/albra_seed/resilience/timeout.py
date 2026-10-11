"""TimeoutPolicy + TimeoutExecutor — wraps async calls with asyncio.wait_for.

The TimeoutPolicy carries a per-call `timeout_seconds` and an optional
`on_timeout` callback invoked when a call times out (best-effort, never
re-raises). The TimeoutExecutor converts `asyncio.TimeoutError` into a
typed `TimeoutExecutorError`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, TypeVar

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now

T = TypeVar("T")


class TimeoutExecutorError(Exception):
    """Raised by TimeoutExecutor when a call times out."""


class TimeoutPolicy(BaseModel):
    timeout_seconds: float = 30.0
    on_timeout: str | None = None  # name of registered callback (set later)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class TimeoutExecutor:
    """Runs an async callable under a timeout."""

    def __init__(self, policy: TimeoutPolicy | None = None) -> None:
        self.policy: TimeoutPolicy = policy or TimeoutPolicy()
        self._callbacks: dict[str, Callable[[str], Awaitable[None] | None]] = {}

    def register_callback(
        self, name: str, fn: Callable[[str], Awaitable[None] | None]
    ) -> None:
        """Register a callback invoked when a timeout occurs.

        The callback receives a label (caller-supplied at `execute` time).
        """
        self._callbacks[name] = fn

    async def execute(
        self,
        fn: Callable[[], Awaitable[T]],
        *,
        timeout: float | None = None,
        label: str = "",
    ) -> T:
        seconds = timeout if timeout is not None else self.policy.timeout_seconds
        try:
            return await asyncio.wait_for(fn(), timeout=seconds)
        except asyncio.TimeoutError as exc:
            cb_name = self.policy.on_timeout
            if cb_name and cb_name in self._callbacks:
                cb = self._callbacks[cb_name]
                try:
                    result = cb(label)
                    if hasattr(result, "__await__"):
                        await result  # type: ignore[misc]
                except Exception:  # noqa: BLE001
                    # callbacks must never break the executor.
                    pass
            raise TimeoutExecutorError(
                f"call '{label}' timed out after {seconds}s"
            ) from exc


__all__ = ["TimeoutPolicy", "TimeoutExecutor", "TimeoutExecutorError"]
