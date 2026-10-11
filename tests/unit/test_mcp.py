"""Unit tests for the MCP subpackage."""

import asyncio

import pytest

from albra_seed.mcp import (
    InMemoryMCPClient,
    InMemoryMCPServer,
    MCPCapability,
    MCPClient,
    MCPInvocationError,
    MCPRegistry,
    MCPRequest,
    MCPResponse,
    MCPServer,
    MCPValidator,
)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _make_server_with_caps() -> InMemoryMCPServer:
    server = InMemoryMCPServer("test-server")

    def echo(args: dict) -> dict:
        return {"echoed": args.get("message")}

    async def slow_add(args: dict) -> dict:
        await asyncio.sleep(0.01)
        return {"sum": args.get("a", 0) + args.get("b", 0)}

    server.register_capability(
        "echo",
        echo,
        description="echoes the input message",
        tags=["utility"],
    )
    server.register_capability(
        "add", slow_add, description="adds two ints", is_async=True
    )
    return server


# -------------------------------------------------------------------
# 1. Server registers capability
# -------------------------------------------------------------------


def test_mcp_server_registers_capability():
    server = _make_server_with_caps()
    caps = server.list_capabilities()
    names = [c.name for c in caps]
    assert "echo" in names
    assert "add" in names

    echo_cap = server.get_capability("echo")
    assert isinstance(echo_cap, MCPCapability)
    assert echo_cap.description == "echoes the input message"
    assert "utility" in echo_cap.tags


# -------------------------------------------------------------------
# 2. Client discovers capabilities
# -------------------------------------------------------------------


def test_mcp_client_discovers_capabilities():
    server = _make_server_with_caps()
    client = InMemoryMCPClient()

    connected = asyncio.run(client.connect(server))
    assert connected
    assert client.is_connected

    caps = asyncio.run(client.discover())
    names = sorted(c.name for c in caps)
    assert names == ["add", "echo"]


# -------------------------------------------------------------------
# 3. Client calls capability
# -------------------------------------------------------------------


def test_mcp_client_calls_capability():
    server = _make_server_with_caps()
    client = InMemoryMCPClient()
    asyncio.run(client.connect(server))

    result = asyncio.run(client.call("echo", {"message": "hello"}))
    assert result == {"echoed": "hello"}

    # Unknown capability surfaces a non-success response without raising
    # when using call_raw.
    raw = asyncio.run(client.call_raw("does-not-exist", {}))
    assert not raw.success
    assert raw.error is not None

    # And `call()` raises for unknown capabilities.
    with pytest.raises(MCPInvocationError):
        asyncio.run(client.call("does-not-exist", {}))


# -------------------------------------------------------------------
# 4. Registry records calls
# -------------------------------------------------------------------


def test_mcp_registry_records_calls():
    server = _make_server_with_caps()
    registry = MCPRegistry()
    registry.register_server("primary", server)

    # Audited call returns the MCPResponse and records an audit entry.
    resp = asyncio.run(
        registry.call("primary", "add", {"a": 2, "b": 3}, timeout_seconds=5)
    )
    assert resp.success
    assert resp.result == {"sum": 5}

    records = registry.audit_records()
    assert len(records) == 1
    assert records[0].server_name == "primary"
    assert records[0].capability == "add"
    assert records[0].success is True
    assert records[0].duration_ms is not None
    assert records[0].duration_ms > 0

    # Unknown server yields a non-success response + audit entry.
    resp2 = asyncio.run(registry.call("missing", "echo", {}))
    assert not resp2.success
    assert len(registry.audit_records()) == 2


# -------------------------------------------------------------------
# 5. Validator returns False for unreachable client
# -------------------------------------------------------------------


def test_mcp_validator_returns_false_for_unreachable():
    validator = MCPValidator()

    # Disconnected client -> not reachable.
    client = InMemoryMCPClient()
    result = asyncio.run(validator.validate_client(client))
    assert result.reachable is False
    assert result.ok is False

    # Connected client -> reachable, healthy.
    server = _make_server_with_caps()
    asyncio.run(client.connect(server))
    result_ok = asyncio.run(validator.validate_client(client))
    assert result_ok.reachable is True
    assert result_ok.healthy is True
    assert "echo" in result_ok.capabilities_seen

    # Server-side validator.
    server_result = asyncio.run(validator.validate_server(server))
    assert server_result.reachable is True
    assert server_result.healthy is True
    assert len(server_result.capabilities_seen) == 2


# -------------------------------------------------------------------
# Extra: timeout + async handlers + invocation error
# -------------------------------------------------------------------


def test_mcp_server_timeout_via_request_field():
    server = InMemoryMCPServer("timeout-test")

    async def slow_handler(args: dict) -> dict:
        await asyncio.sleep(0.5)
        return {"ok": True}

    server.register_capability("slow", slow_handler, is_async=True)
    req = MCPRequest(capability="slow", arguments={}, timeout_seconds=0.05)
    resp = asyncio.run(server.handle_request(req))
    assert not resp.success
    assert resp.error is not None


def test_mcp_client_is_not_connected_after_disconnect():
    server = _make_server_with_caps()
    client = InMemoryMCPClient()
    asyncio.run(client.connect(server))
    assert client.is_connected is True
    client.disconnect()
    assert client.is_connected is False
    assert asyncio.run(client.health_check()) is False
