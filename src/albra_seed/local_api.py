"""Loopback-friendly ASGI entrypoint for local development.

Run with: uvicorn albra_seed.local_api:app --host 127.0.0.1 --port 8000
This module does not load .env files or configure provider credentials.
"""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import AsyncIterator
from albra_seed import Runtime, SeedConfig, create_app

runtime = Runtime(config=SeedConfig())
app = create_app(runtime)

@asynccontextmanager
async def lifespan(_app: object) -> AsyncIterator[None]:
    """Start and stop the runtime with the ASGI server lifecycle."""
    await runtime.startup()
    try:
        yield
    finally:
        await runtime.shutdown()

# The embedding application owns the runtime lifecycle policy.
app.router.lifespan_context = lifespan
