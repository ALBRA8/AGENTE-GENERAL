"""MCP client interface — consumes capabilities from external MCP servers.

The default `InMemoryMCPClient` connects directly to an `MCPServer`
instance (in-process). Real transports (HTTP/JSON-RPC, stdio MCP) can be
implemented by overriding `connect`, `discover`, `call` and
`health_check`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from albra_seed.mcp.protocol import (
    ERR_UNREACHABLE,
    MCPCapability,
    MCPRequest,
    MCPResponse,
)
from albra_seed.mcp.server import MCPServer


class MCPClient(ABC):
    """Abstract MCP client."""

    client_id: str

    @abstractmethod
    async def connect(self, server_url: str | MCPServer) -> bool:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def discover(self) -> list[MCPCapability]:
        raise NotImplementedError

    @abstractmethod
    async def call(self, capability_name: str, args: dict[str, Any]) -> Any:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        raise NotImplementedError


class InMemoryMCPClient(MCPClient):
    """In-process MCP client. `server_url` is interpreted as:

    * an `MCPServer` instance — used directly (in-process);
    * the literal string `"inmemory"` — client starts disconnected.

    For real network transports, subclass and override `connect`.
    """

    def __init__(self, client_id: str = "inmemory-mcp-client") -> None:
        self.client_id = client_id
        self._server: MCPServer | None = None
        self._server_url: str | None = None

    # -- connection lifecycle ---------------------------------------
    async def connect(self, server_url: str | MCPServer) -> bool:
        if isinstance(server_url, MCPServer):
            self._server = server_url
            self._server_url = f"inmemory://{server_url.server_id}"
            return await server_url.health()
        # Network transport hooks — default returns False for unknown URLs.
        self._server = None
        self._server_url = None
        return False

    def disconnect(self) -> None:
        self._server = None
        self._server_url = None

    @property
    def is_connected(self) -> bool:
        return self._server is not None

    @property
    def server_url(self) -> str | None:
        return self._server_url

    # -- discovery + call -------------------------------------------
    async def discover(self) -> list[MCPCapability]:
        if self._server is None:
            return []
        return self._server.list_capabilities()

    async def call(self, capability_name: str, args: dict[str, Any]) -> Any:
        response = await self.call_raw(capability_name, args)
        if not response.success:
            raise MCPInvocationError(
                capability_name,
                response.error or "unknown error",
                response.error_code,
            )
        return response.result

    async def call_raw(
        self,
        capability_name: str,
        args: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> MCPResponse:
        """Returns the full MCPResponse envelope (no raise on error)."""
        if self._server is None:
            return MCPResponse.error_response(
                request_id="no-connection",
                error="client is not connected to any MCP server",
                error_code=ERR_UNREACHABLE,
            )
        request = MCPRequest(
            capability=capability_name,
            arguments=dict(args or {}),
            requester=self.client_id,
            timeout_seconds=timeout_seconds,
        )
        return await self._server.handle_request(request)

    async def health_check(self) -> bool:
        if self._server is None:
            return False
        try:
            return await self._server.health()
        except Exception:  # pragma: no cover - defensive
            return False


class MCPInvocationError(RuntimeError):
    """Raised when `call()` returns a non-success response."""

    def __init__(
        self,
        capability: str,
        message: str,
        error_code: int | None = None,
    ) -> None:
        self.capability = capability
        self.error_code = error_code
        super().__init__(f"MCP call '{capability}' failed [{error_code}]: {message}")


__all__ = ["MCPClient", "InMemoryMCPClient", "MCPInvocationError"]
