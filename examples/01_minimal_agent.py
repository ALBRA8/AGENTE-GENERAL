#!/usr/bin/env python3
"""01_minimal_agent.py — Minimal CORE_ONLY agent run.

Demonstrates the smallest possible end-to-end ALBRA agent run:
    1. Build a SeedConfig (CORE_ONLY by default).
    2. Start the Runtime.
    3. Register the ProviderRouter backed by MockProvider.
    4. Build an AgentIdentity + Agent.
    5. Run the agent with the input "hello".
    6. Print the response content.
    7. Shutdown cleanly.

NO external credentials required — Mock provider is always available.

Run:
    python examples/01_minimal_agent.py
"""

from __future__ import annotations

import asyncio

from albra_seed import Agent, AgentIdentity, Runtime, SeedConfig, AutonomyLevel
from albra_seed.providers.mock import MockProvider
from albra_seed.providers.registry import ProviderRegistry
from albra_seed.providers.router import ProviderRouter
from albra_seed.runtime.runtime import COMPONENT_PROVIDER_ROUTER


async def main() -> None:
    # 1. CORE_ONLY config (no env vars needed).
    config = SeedConfig()
    runtime = Runtime(config=config)
    await runtime.startup()

    # 2. Wire up the ProviderRouter with MockProvider as the only backend.
    registry = ProviderRegistry()
    registry.register(MockProvider())
    router = ProviderRouter(registry=registry, default_provider="mock")
    runtime.register(COMPONENT_PROVIDER_ROUTER, router)

    # 3. Build a minimal agent identity at L2 (execute-safe autonomy).
    identity = AgentIdentity(
        name="minimal-agent",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        providers=["mock"],
        permissions=["tool.echo.execute"],
    )
    agent = Agent(identity=identity, runtime=runtime)

    # 4. Run the universal agent loop end-to-end.
    response = await agent.run("hello")
    print(f"content         : {response.content!r}")
    print(f"execution_id    : {response.execution_id}")
    print(f"correlation_id  : {response.correlation_id}")
    print(f"confidence      : {response.confidence}")
    print(f"warnings        : {response.warnings}")
    print(f"errors          : {response.errors}")

    # 5. Shutdown cleanly.
    await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
