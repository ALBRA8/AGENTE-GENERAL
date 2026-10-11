"""GracefulDegradation — registers fallback handlers per component.

When a component (provider, tool, plugin, ...) fails, the corresponding
fallback handler is invoked instead. The fallback is best-effort: it
must never raise. If the fallback itself raises, the original exception
is re-raised.

This is the universal mechanism that prevents one failing component
from bringing down the whole agent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")

log = logging.getLogger("albra.resilience.degradation")


FallbackHandler = Callable[[BaseException], Awaitable[Any] | Any]


class GracefulDegradation:
    """Per-component fallback registry."""

    def __init__(self) -> None:
        self._handlers: dict[str, FallbackHandler] = {}
        self._degraded: dict[str, int] = {}  # component -> # invocations
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(self, component: str, handler: FallbackHandler) -> None:
        self._handlers[component] = handler

    def unregister(self, component: str) -> bool:
        return self._handlers.pop(component, None) is not None

    def has_handler(self, component: str) -> bool:
        return component in self._handlers

    def list_components(self) -> list[str]:
        return list(self._handlers.keys())

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------
    async def call_with_fallback(
        self,
        component: str,
        fn: Callable[[], Awaitable[T]],
    ) -> T:
        """Call `fn`; if it raises and a handler for `component` is
        registered, invoke the handler. If the handler raises, the
        original exception is re-raised.
        """
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            handler = self._handlers.get(component)
            if handler is None:
                raise
            async with self._lock:
                self._degraded[component] = self._degraded.get(component, 0) + 1
            try:
                result = handler(exc)
                if hasattr(result, "__await__"):
                    result = await result  # type: ignore[misc]
                return result  # type: ignore[return-value]
            except Exception:  # noqa: BLE001
                # Fallback failed — re-raise original.
                raise exc

    async def invoke_fallback(self, component: str, error: BaseException) -> Any:
        """Directly invoke a registered fallback handler. Returns None if
        no handler is registered."""
        handler = self._handlers.get(component)
        if handler is None:
            return None
        async with self._lock:
            self._degraded[component] = self._degraded.get(component, 0) + 1
        try:
            result = handler(error)
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[misc]
            return result
        except Exception as exc:  # noqa: BLE001
            log.warning("fallback for '%s' raised: %s", component, exc)
            return None

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def degraded_count(self, component: str) -> int:
        return self._degraded.get(component, 0)

    def is_degraded(self, component: str) -> bool:
        return self.degraded_count(component) > 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "components": list(self._handlers.keys()),
            "degraded": dict(self._degraded),
        }


__all__ = ["GracefulDegradation", "FallbackHandler"]
