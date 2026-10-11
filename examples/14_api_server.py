#!/usr/bin/env python3
"""14_api_server.py — Start the REST API server (requires fastapi + uvicorn).

Demonstrates the FastAPI REST API:
    1. Build a CORE_ONLY Runtime with the ProviderRouter wired.
    2. Create the FastAPI app via create_app(runtime).
    3. Use FastAPI's TestClient to probe /api/v1/health + /api/v1/readiness.
    4. Print the response bodies.
    5. (Optional) Start the uvicorn server for live testing — see the
       commented-out `server.start()` call at the bottom.

Requires:
    pip install fastapi uvicorn httpx

Run:
    python examples/14_api_server.py
"""

from __future__ import annotations

import asyncio

from albra_seed import Runtime, SeedConfig
from albra_seed.providers.mock import MockProvider
from albra_seed.providers.registry import ProviderRegistry
from albra_seed.providers.router import ProviderRouter
from albra_seed.runtime.runtime import COMPONENT_PROVIDER_ROUTER


async def main() -> None:
    # 1. CORE_ONLY runtime + ProviderRouter.
    runtime = Runtime(config=SeedConfig())
    await runtime.startup()
    reg = ProviderRegistry(); reg.register(MockProvider())
    runtime.register(COMPONENT_PROVIDER_ROUTER, ProviderRouter(registry=reg))

    # 2. Create the FastAPI app (lazy-imported; raises clean ImportError if missing).
    from albra_seed import create_app
    app = create_app(runtime)

    # 3. Probe endpoints via TestClient (no live server needed).
    from fastapi.testclient import TestClient
    client = TestClient(app)

    health = client.get("/api/v1/health")
    print(f"GET /api/v1/health   → {health.status_code}")
    print(f"                      body: {health.json()}")

    readiness = client.get("/api/v1/readiness")
    print(f"\nGET /api/v1/readiness → {readiness.status_code}")
    print(f"                      body: {readiness.json()}")

    root = client.get("/")
    print(f"\nGET /                → {root.status_code}")
    print(f"                      body: {root.json()}")

    await runtime.shutdown()

    # 4. (Optional) To run a live server, uncomment:
    # from albra_seed.api.server import APIServer
    # server = APIServer(runtime, host="127.0.0.1", port=8000)
    # server.start()   # blocks; Ctrl-C to stop


if __name__ == "__main__":
    asyncio.run(main())
