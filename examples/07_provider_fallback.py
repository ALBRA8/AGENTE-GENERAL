#!/usr/bin/env python3
"""07_provider_fallback.py — Register a fake provider that fails, fallback to mock.

Demonstrates provider routing + fallback:
    1. Build a ProviderRegistry with a "failing" provider + MockProvider.
    2. The failing provider raises on every complete() call.
    3. ProviderRouter.complete() transparently falls back to Mock.
    4. The router's transitions log records the failure → fallback.

Run:
    python examples/07_provider_fallback.py
"""

from __future__ import annotations

import asyncio

from albra_seed import AgentResponse, Message, RunContext
from albra_seed.core.base import LLMProvider
from albra_seed.providers.fallback import ProviderFallback
from albra_seed.providers.mock import MockProvider
from albra_seed.providers.registry import ProviderRegistry


class FailingProvider(LLMProvider):
    """A provider that always raises — simulates a down upstream API."""

    name = "failing"
    is_mock = False
    is_external = True

    async def complete(self, messages, *, model=None, temperature=0.2,
                      max_tokens=2048, tools=None, context=None) -> AgentResponse:
        raise RuntimeError("simulated upstream outage")

    async def health(self) -> bool:
        return False


async def main() -> None:
    # 1. Build registry: failing provider + Mock fallback.
    registry = ProviderRegistry()
    registry.register(FailingProvider())
    registry.register(MockProvider())

    # 2. Build fallback chain: try "failing" first, then "mock".
    chain = ProviderFallback(["failing", "mock"], registry)

    # 3. Complete a request — should fall back to Mock.
    response = await chain.complete(
        [Message(role="user", content="hello")],
        context=RunContext(agent_id="demo", task="fallback"),
    )
    print(f"content          : {response.content!r}")
    print(f"providers used   : {response.metadata}")

    # 4. Inspect the recorded transitions.
    print(f"transitions      : {len(chain.transitions)}")
    for t in chain.transitions:
        print(f"  - {t['provider']:8s} {t['kind']:10s} err={t['error'][:50]}")

    # 5. The Mock fallback must have produced a valid response.
    assert response.content.startswith("[mock]")


if __name__ == "__main__":
    asyncio.run(main())
