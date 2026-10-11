"""Recovery strategies — safe, declarative recovery primitives.

Every strategy MUST be safe: no destructive side effects (no `rm -rf`,
no dropping DB tables, no force-killing other processes). Dangerous
recovery actions (delete-and-recreate, wipe cache, restart host) are
INTENTIONALLY NOT provided as strategies — they require explicit human
approval via the approvals subsystem.

Concrete strategies:
  * RetryStrategy              — re-invoke the callable up to N times with
                                  exponential backoff. Good for TRANSIENT
                                  / EXTERNAL.
  * FallbackStrategy            — invoke a fallback callable when the
                                  primary fails. Good for PERSISTENT.
  * RestartComponentStrategy    — invoke a registered "restart" callable
                                  for the failing component. NEVER kills
                                  the process; just calls the provided
                                  hook. Good for RESOURCE / PERSISTENT.
  * GracefulDegradationStrategy — return a safe default value, mark the
                                  component as degraded. Good for any
                                  class when no better option exists.
"""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

from albra_seed.recovery.classifier import ErrorClass


class RecoveryStrategy(ABC):
    """Abstract recovery strategy.

    Every strategy is async; takes the failing callable + the exception;
    returns a result (or raises).
    """

    name: str = "abstract"

    @abstractmethod
    async def execute(
        self,
        fn: Callable[[], Awaitable[Any]],
        exc: BaseException,
        *,
        component: str | None = None,
    ) -> Any:
        raise NotImplementedError


class RecoveryResult:
    """Wrapped recovery outcome (used for introspection / audit)."""

    __slots__ = ("value", "applied_strategy", "success", "attempts", "error")

    def __init__(
        self,
        *,
        value: Any = None,
        applied_strategy: str = "",
        success: bool = True,
        attempts: int = 0,
        error: str | None = None,
    ) -> None:
        self.value = value
        self.applied_strategy = applied_strategy
        self.success = success
        self.attempts = attempts
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied_strategy": self.applied_strategy,
            "success": self.success,
            "attempts": self.attempts,
            "error": self.error,
        }


class RetryStrategy(RecoveryStrategy):
    """Re-invoke the callable with exponential backoff + jitter."""

    name = "retry"

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay_seconds: float = 0.1,
        max_delay_seconds: float = 5.0,
        retry_on: set[ErrorClass] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.max_attempts = max_attempts
        self.base_delay = base_delay_seconds
        self.max_delay = max_delay_seconds
        self.retry_on = retry_on or {ErrorClass.TRANSIENT, ErrorClass.EXTERNAL}

    async def execute(
        self,
        fn: Callable[[], Awaitable[Any]],
        exc: BaseException,
        *,
        component: str | None = None,
    ) -> Any:
        last_exc = exc
        # First attempt is the recovery attempt (the original call already
        # failed before this strategy was invoked).
        for attempt in range(self.max_attempts):
            try:
                return await fn()
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if attempt < self.max_attempts - 1:
                    delay = min(
                        self.max_delay,
                        self.base_delay * (2 ** attempt)
                        + random.uniform(0, self.base_delay),
                    )
                    await asyncio.sleep(delay)
        raise last_exc


class FallbackStrategy(RecoveryStrategy):
    """Invoke a fallback callable when the primary fails."""

    name = "fallback"

    def __init__(self, fallback: Callable[[], Awaitable[Any]]) -> None:
        self._fallback = fallback

    async def execute(
        self,
        fn: Callable[[], Awaitable[Any]],
        exc: BaseException,
        *,
        component: str | None = None,
    ) -> Any:
        return await self._fallback()


class RestartComponentStrategy(RecoveryStrategy):
    """Invoke a registered "restart" hook for the failing component.

    NEVER kills the host process. The hook is provided by the component
    owner and is responsible for any safe restart logic (e.g. re-create
    a provider client, re-open a connection pool).
    """

    name = "restart_component"

    def __init__(self, restart_hook: Callable[[str], Awaitable[Any] | Any]) -> None:
        self._restart = restart_hook

    async def execute(
        self,
        fn: Callable[[], Awaitable[Any]],
        exc: BaseException,
        *,
        component: str | None = None,
    ) -> Any:
        if component is None:
            raise ValueError("RestartComponentStrategy requires a component name")
        result = self._restart(component)
        if hasattr(result, "__await__"):
            await result
        # After restart, retry the original fn once.
        return await fn()


class GracefulDegradationStrategy(RecoveryStrategy):
    """Return a safe default value; do NOT re-invoke the failing fn."""

    name = "graceful_degradation"

    def __init__(self, default_value: Any = None) -> None:
        self._default = default_value

    async def execute(
        self,
        fn: Callable[[], Awaitable[Any]],
        exc: BaseException,
        *,
        component: str | None = None,
    ) -> Any:
        return self._default


# Mapping from ErrorClass to default strategy KIND (not instance — caller
# supplies the actual instance with concrete parameters).
DEFAULT_STRATEGY_BY_CLASS: dict[ErrorClass, str] = {
    ErrorClass.TRANSIENT: "retry",
    ErrorClass.EXTERNAL: "retry",
    ErrorClass.PERSISTENT: "fallback",
    ErrorClass.RESOURCE: "restart_component",
    ErrorClass.SECURITY: "none",  # NEVER auto-recover
    ErrorClass.UNKNOWN: "none",   # NEVER auto-recover
}


__all__ = [
    "RecoveryStrategy",
    "RecoveryResult",
    "RetryStrategy",
    "FallbackStrategy",
    "RestartComponentStrategy",
    "GracefulDegradationStrategy",
    "DEFAULT_STRATEGY_BY_CLASS",
]
