"""Provider router.

The router is the single entry point for completion requests inside
the runtime. It:

* Prefers an explicitly configured provider/model (set via
  `configure()` or constructor args).
* Falls back to the next available provider when the preferred one is
  unavailable.
* Always terminates at the Mock provider when nothing else works, so
  CORE_ONLY mode never deadlocks.

The router implements `LLMProvider` itself, so the Runtime can register
a `ProviderRouter` instance as the `provider_router` component and
agent code can call `router.complete(...)` directly.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from albra_seed.core.base import LLMProvider
from albra_seed.core.types import (
    AgentResponse,
    Message,
    RunContext,
)
from albra_seed.providers.fallback import ProviderFallback
from albra_seed.providers.registry import ProviderRegistry

log = structlog.get_logger("albra.providers.router")


class ProviderRouter(LLMProvider):
    """Routes `complete()` calls to a configured provider with fallback."""

    name: str = "provider_router"
    is_mock: bool = False
    is_external: bool = False  # composite — not itself external

    def __init__(
        self,
        registry: ProviderRegistry | None = None,
        *,
        default_provider: str | None = None,
        default_model: str | None = None,
        fallback_chain: list[str] | None = None,
    ) -> None:
        self.registry: ProviderRegistry = registry or ProviderRegistry()
        self.default_provider: str | None = default_provider
        self.default_model: str | None = default_model
        self._explicit_chain: list[str] | None = fallback_chain

    # ------------------------------------------------------------------ #
    # Configuration                                                      #
    # ------------------------------------------------------------------ #

    def configure(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        fallback_chain: list[str] | None = None,
    ) -> None:
        """Set routing preferences at runtime."""
        if provider is not None:
            self.default_provider = provider
        if model is not None:
            self.default_model = model
        if fallback_chain is not None:
            self._explicit_chain = list(fallback_chain)
        log.info(
            "router.configured",
            provider=self.default_provider,
            model=self.default_model,
            chain=self._explicit_chain,
        )

    def route(
        self,
        model: str | None = None,
        *,
        preferred: str | None = None,
    ) -> LLMProvider:
        """Return the provider that SHOULD be used for a given request.

        Strategy:
        1. If `preferred` (or `default_provider`) is set AND registered AND
           available, use it.
        2. Otherwise pick the first available non-mock provider.
        3. Otherwise return the Mock provider.
        """
        candidate_order = self._build_candidate_order(preferred)
        for name in candidate_order:
            provider = self.registry.get(name)
            if provider is None:
                continue
            if provider.is_mock:
                continue
            health = self.registry.health_for(name)
            if health is None or health.is_available:
                return provider
        # Fallback to mock — always exists in the registry.
        return self.registry.require("mock")

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        context: RunContext | None = None,
    ) -> AgentResponse:
        """Execute a completion using the fallback chain."""
        chain = self._resolve_chain()
        fb = ProviderFallback(chain, self.registry, name=self.name)
        chosen_model = model or self.default_model
        t0 = time.monotonic()
        response = await fb.complete(
            messages,
            model=chosen_model,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            context=context,
        )
        latency = (time.monotonic() - t0) * 1000.0
        # Tag the response so callers know it came through the router.
        response.metadata["router"] = self.name
        response.metadata["router_latency_ms"] = round(latency, 2)
        return response

    async def health(self) -> bool:
        """Healthy if at least the Mock provider is registered."""
        return self.registry.get("mock") is not None

    # ------------------------------------------------------------------ #
    # Internal                                                          #
    # ------------------------------------------------------------------ #

    def _build_candidate_order(self, preferred: str | None) -> list[str]:
        order: list[str] = []
        head = preferred or self.default_provider
        if head:
            order.append(head)
        # Add all registered non-mock providers in registration order.
        for name in self.registry.list_all():
            if name == "mock" or name in order:
                continue
            order.append(name)
        # Mock is the ultimate fallback.
        order.append("mock")
        return order

    def _resolve_chain(self) -> list[str]:
        if self._explicit_chain:
            return list(self._explicit_chain)
        # Default chain = preferred → all available non-mock → mock.
        chain: list[str] = []
        if self.default_provider:
            chain.append(self.default_provider)
        for name in self.registry.list_available():
            if name == "mock" or name in chain:
                continue
            chain.append(name)
        if "mock" not in chain:
            chain.append("mock")
        return chain

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "default_provider": self.default_provider,
            "default_model": self.default_model,
            "explicit_chain": self._explicit_chain,
            "registry": self.registry.snapshot(),
        }


__all__ = ["ProviderRouter"]
