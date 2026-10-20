"""Fallback chain for provider routing.

A `ProviderFallback` wraps an ordered list of provider names. On
`complete()`, it tries each in turn; on failure (exception or response
with errors), it logs the transition and moves to the next. The chain
always terminates with the Mock provider if no name resolves to a
working provider.
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
from albra_seed.providers.registry import ProviderRegistry
from albra_seed.security.output_redaction import redact_secrets

log = structlog.get_logger("albra.providers.fallback")


class ProviderFallback:
    """Ordered chain of providers used as a resilient completion backend."""

    def __init__(
        self,
        chain: list[str],
        registry: ProviderRegistry,
        *,
        name: str = "fallback",
    ) -> None:
        if not chain:
            raise ValueError("fallback chain must have at least one provider")
        # Mock is always implicitly appended if missing.
        if "mock" not in chain:
            chain = [*chain, "mock"]
        self.chain: list[str] = chain
        self.registry: ProviderRegistry = registry
        self.name: str = name
        self.transitions: list[dict[str, Any]] = []

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
        """Try each provider in chain order; fall back on failure."""
        last_error: str | None = None
        for idx, name in enumerate(self.chain):
            provider = self.registry.get(name)
            if provider is None:
                continue
            t0 = time.monotonic()
            try:
                response = await provider.complete(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                    context=context,
                )
                latency = (time.monotonic() - t0) * 1000.0
                # If response has explicit errors, treat as failure.
                if response.errors:
                    last_error = "; ".join(redact_secrets(str(e)) for e in response.errors)
                    self._record_transition(name, "error", latency, last_error, idx)
                    health = self.registry.health_for(name)
                    if health is not None:
                        health.record_error(latency, last_error)
                    continue
                health = self.registry.health_for(name)
                if health is not None:
                    health.record_success(latency)
                if context is not None:
                    context.providers_used.append(name)
                if idx > 0:
                    log.info(
                        "provider.fallback.succeeded_after_failures",
                        provider=name,
                        attempted=self.chain[: idx + 1],
                    )
                return response
            except Exception as exc:
                latency = (time.monotonic() - t0) * 1000.0
                last_error = redact_secrets(f"{type(exc).__name__}: {exc}")
                self._record_transition(name, "exception", latency, last_error, idx)
                health = self.registry.health_for(name)
                if health is not None:
                    health.record_error(latency, last_error)
                continue

        # Should be unreachable because Mock is always in the chain.
        raise RuntimeError(f"all providers failed; last_error={last_error}")

    def _record_transition(
        self,
        provider: str,
        kind: str,
        latency_ms: float,
        error: str,
        index: int,
    ) -> None:
        entry = {
            "provider": provider,
            "kind": kind,
            "latency_ms": round(latency_ms, 2),
            "error": error,
            "chain_index": index,
        }
        self.transitions.append(entry)
        log.warning("provider.fallback.transition", **entry)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "chain": list(self.chain),
            "transitions": list(self.transitions),
        }


__all__ = ["ProviderFallback"]
