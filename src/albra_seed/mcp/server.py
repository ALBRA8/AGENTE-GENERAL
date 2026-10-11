"""MCP server interface — exposes agent capabilities to external MCP clients.

Concrete `InMemoryMCPServer` keeps everything in-process; specialisation
layers can swap in a real JSON-RPC/HTTP transport without touching the
client or registry code.
"""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Awaitable, Callable, Union

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now
from albra_seed.mcp.protocol import (
    ERR_INTERNAL_ERROR,
    ERR_METHOD_NOT_FOUND,
    ERR_TIMEOUT,
    MCPCapability,
    MCPRequest,
    MCPResponse,
)

# A capability handler is either a sync callable or a coroutine returning Any.
CapabilityHandler = Callable[[dict[str, Any]], Union[Any, Awaitable[Any]]]


class MCPServer(ABC):
    """Abstract MCP server. Capabilities are registered as (name, handler)
    pairs; the server then dispatches incoming MCPRequest envelopes."""

    server_id: str

    @abstractmethod
    def register_capability(
        self,
        name: str,
        handler: CapabilityHandler,
        *,
        description: str = "",
        version: str = "1.0.0",
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        is_async: bool = False,
        tags: list[str] | None = None,
    ) -> MCPCapability:
        raise NotImplementedError

    @abstractmethod
    def unregister_capability(self, name: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def list_capabilities(self) -> list[MCPCapability]:
        raise NotImplementedError

    @abstractmethod
    async def handle_request(self, request: MCPRequest) -> MCPResponse:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> bool:
        raise NotImplementedError


class _Registration(BaseModel):
    capability: MCPCapability
    handler: Any  # CapabilityHandler — kept untyped to allow sync/async
    registered_at: datetime = Field(default_factory=utc_now)


class InMemoryMCPServer(MCPServer):
    """In-process MCPServer. Handlers may be sync or async; both are awaited
    transparently. Unknown capabilities return ERR_METHOD_NOT_FOUND."""

    def __init__(self, server_id: str = "inmemory-mcp") -> None:
        self.server_id = server_id
        self._caps: dict[str, _Registration] = {}

    # -- registration -------------------------------------------------
    def register_capability(
        self,
        name: str,
        handler: CapabilityHandler,
        *,
        description: str = "",
        version: str = "1.0.0",
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        is_async: bool = False,
        tags: list[str] | None = None,
    ) -> MCPCapability:
        if not callable(handler):
            raise TypeError("handler must be callable")
        cap = MCPCapability(
            name=name,
            description=description,
            version=version,
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            is_async=is_async,
            tags=tags or [],
        )
        self._caps[name] = _Registration(capability=cap, handler=handler)
        return cap

    def unregister_capability(self, name: str) -> bool:
        return self._caps.pop(name, None) is not None

    def list_capabilities(self) -> list[MCPCapability]:
        return [reg.capability for reg in self._caps.values()]

    def get_capability(self, name: str) -> MCPCapability | None:
        reg = self._caps.get(name)
        return reg.capability if reg else None

    # -- dispatch ----------------------------------------------------
    async def handle_request(self, request: MCPRequest) -> MCPResponse:
        reg = self._caps.get(request.capability)
        if reg is None:
            return MCPResponse.error_response(
                request.request_id,
                f"capability not found: {request.capability}",
                error_code=ERR_METHOD_NOT_FOUND,
            )
        try:
            result = reg.handler(request.arguments)
            if inspect.isawaitable(result):
                if request.timeout_seconds is not None:
                    result = await asyncio.wait_for(
                        result, timeout=request.timeout_seconds
                    )
                else:
                    result = await result
        except asyncio.TimeoutError:
            return MCPResponse.error_response(
                request.request_id,
                f"capability timed out: {request.capability}",
                error_code=ERR_TIMEOUT,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return MCPResponse.error_response(
                request.request_id,
                f"capability error: {exc}",
                error_code=ERR_INTERNAL_ERROR,
            )
        return MCPResponse.ok(request.request_id, result)

    async def health(self) -> bool:
        return True


__all__ = ["MCPServer", "InMemoryMCPServer", "CapabilityHandler"]
