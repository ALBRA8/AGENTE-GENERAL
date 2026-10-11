"""Provider registry.

A central store for `LLMProvider` instances. Keeps per-provider
`ProviderHealth` records alongside the provider instance and provides
lookup, listing, and aggregate health check operations.

The registry is intentionally a plain Python object — it carries no
global state and is owned by the Runtime (or by the test harness).
"""

from __future__ import annotations

from typing import Any

import structlog

from albra_seed.core.base import LLMProvider
from albra_seed.providers.health import ProviderHealth
from albra_seed.providers.mock import MockProvider

log = structlog.get_logger("albra.providers.registry")


class ProviderRegistry:
    """In-memory registry of LLM providers with per-provider health."""

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}
        self._health: dict[str, ProviderHealth] = {}
        # Mock is always pre-registered so CORE_ONLY mode has a fallback.
        self._ensure_mock()

    # ------------------------------------------------------------------ #
    # Mutators                                                          #
    # ------------------------------------------------------------------ #

    def register(self, provider: LLMProvider) -> None:
        """Register a provider. Replaces any with the same name."""
        name = provider.name
        self._providers[name] = provider
        if name not in self._health:
            self._health[name] = ProviderHealth(name=name)
        log.info("provider.registered", name=name, mock=provider.is_mock,
                 external=provider.is_external)

    def unregister(self, name: str) -> bool:
        """Remove a provider by name. Returns True if it was present.

        The Mock provider cannot be unregistered — CORE_ONLY mode
        requires it.
        """
        if name == "mock":
            log.warning("provider.unregister_mock_denied")
            return False
        removed = self._providers.pop(name, None) is not None
        self._health.pop(name, None)
        return removed

    def _ensure_mock(self) -> None:
        if "mock" not in self._providers:
            self._providers["mock"] = MockProvider()
            self._health["mock"] = ProviderHealth(name="mock")
            self._health["mock"].mark_healthy(reason="always-on")

    # ------------------------------------------------------------------ #
    # Lookups                                                            #
    # ------------------------------------------------------------------ #

    def get(self, name: str) -> LLMProvider | None:
        return self._providers.get(name)

    def require(self, name: str) -> LLMProvider:
        provider = self.get(name)
        if provider is None:
            raise KeyError(f"provider not registered: {name}")
        return provider

    def list_available(self) -> list[str]:
        """List provider names whose health state permits routing."""
        return [
            name
            for name, prov in self._providers.items()
            if self._health[name].is_available or prov.is_mock
        ]

    def list_all(self) -> list[str]:
        return list(self._providers.keys())

    def health_for(self, name: str) -> ProviderHealth | None:
        return self._health.get(name)

    def all_health(self) -> dict[str, ProviderHealth]:
        return dict(self._health)

    # ------------------------------------------------------------------ #
    # Aggregate health check                                            #
    # ------------------------------------------------------------------ #

    async def health_check_all(self) -> dict[str, dict[str, Any]]:
        """Call `health()` on every registered provider and record latency.

        Returns a snapshot dict keyed by provider name.
        """
        import time

        snapshots: dict[str, dict[str, Any]] = {}
        for name, provider in list(self._providers.items()):
            t0 = time.monotonic()
            try:
                ok = await provider.health()
                latency = (time.monotonic() - t0) * 1000.0
                health = self._health.get(name)
                if health is not None:
                    if ok:
                        health.record_success(latency)
                    else:
                        health.record_error(latency, "health_check_failed")
                snapshots[name] = {
                    "healthy": ok,
                    "latency_ms": round(latency, 2),
                    "state": health.state.value if health else "unknown",
                }
            except Exception as exc:
                latency = (time.monotonic() - t0) * 1000.0
                health = self._health.get(name)
                if health is not None:
                    health.record_error(latency, str(exc))
                snapshots[name] = {
                    "healthy": False,
                    "latency_ms": round(latency, 2),
                    "state": health.state.value if health else "unknown",
                    "error": str(exc),
                }
        return snapshots

    def snapshot(self) -> dict[str, Any]:
        return {
            "providers": self.list_all(),
            "available": self.list_available(),
            "health": {n: h.snapshot() for n, h in self._health.items()},
        }


__all__ = ["ProviderRegistry"]
