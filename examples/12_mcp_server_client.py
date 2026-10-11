#!/usr/bin/env python3
"""12_mcp_server_client.py — Set up an in-memory MCP server + client.

Demonstrates MCP (Model Context Protocol):
    1. Build an InMemoryMCPServer and register a capability.
    2. Build an InMemoryMCPClient and connect it to the server.
    3. Discover capabilities via the client.
    4. Invoke the capability via the client and print the result.

Run:
    python examples/12_mcp_server_client.py
"""

from __future__ import annotations

import asyncio

from albra_seed.mcp.client import InMemoryMCPClient
from albra_seed.mcp.server import InMemoryMCPServer


async def main() -> None:
    # 1. Build a server + register a capability.
    server = InMemoryMCPServer(server_id="demo-mcp")

    async def lookup_handler(args):
        # Generic capability — NO domain knowledge.
        key = args.get("key", "")
        return {"key": key, "value": f"mock-value-for-{key}"}

    server.register_capability(
        name="lookup",
        handler=lookup_handler,
        description="Look up a key in the in-memory store.",
        is_async=True,
    )

    # 2. Build a client + connect to the server.
    client = InMemoryMCPClient(client_id="demo-client")
    connected = await client.connect(server)
    print(f"connected         : {connected}")
    print(f"is_connected      : {client.is_connected}")

    # 3. Discover capabilities.
    caps = await client.discover()
    print(f"\ndiscovered {len(caps)} capability(caps):")
    for c in caps:
        print(f"  - {c.name}: {c.description}")

    # 4. Invoke the capability.
    result = await client.call("lookup", {"key": "alpha"})
    print(f"\nlookup result     : {result}")

    # 5. Health check.
    healthy = await client.health_check()
    print(f"server healthy    : {healthy}")

    client.disconnect()
    print(f"after disconnect  : is_connected={client.is_connected}")


if __name__ == "__main__":
    asyncio.run(main())
