"""MCP protocol primitives — JSON-RPC-like request/response/capability models.

The protocol layer is intentionally simple: an MCPRequest identifies a
capability by name and carries a JSON-serializable arguments dict; an
MCPResponse carries either a result or an error plus status metadata.
Capabilities are advertised as MCPCapability records.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now


class MCPCapability(BaseModel):
    """Advertisement of a single capability exposed by an MCP server."""

    name: str
    description: str = ""
    version: str = "1.0.0"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    is_async: bool = False
    tags: list[str] = Field(default_factory=list)


class MCPRequest(BaseModel):
    """JSON-RPC-like request envelope."""

    request_id: str = Field(default_factory=lambda: f"req_{uuid4().hex}")
    capability: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)
    requester: str | None = None
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def method(self) -> str:
        """JSON-RPC-style alias for `capability`."""
        return self.capability


class MCPResponse(BaseModel):
    """JSON-RPC-like response envelope."""

    request_id: str
    success: bool
    result: Any = None
    error: str | None = None
    error_code: int | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    duration_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        request_id: str,
        result: Any,
        *,
        duration_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "MCPResponse":
        return cls(
            request_id=request_id,
            success=True,
            result=result,
            duration_ms=duration_ms,
            metadata=metadata or {},
        )

    @classmethod
    def error_response(
        cls,
        request_id: str,
        error: str,
        *,
        error_code: int = -32000,
        metadata: dict[str, Any] | None = None,
    ) -> "MCPResponse":
        return cls(
            request_id=request_id,
            success=False,
            error=error,
            error_code=error_code,
            metadata=metadata or {},
        )


# Standard JSON-RPC error codes used by MCP transports.
ERR_PARSE_ERROR = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL_ERROR = -32603
ERR_TIMEOUT = -32001
ERR_UNREACHABLE = -32002


__all__ = [
    "MCPCapability",
    "MCPRequest",
    "MCPResponse",
    "ERR_PARSE_ERROR",
    "ERR_INVALID_REQUEST",
    "ERR_METHOD_NOT_FOUND",
    "ERR_INVALID_PARAMS",
    "ERR_INTERNAL_ERROR",
    "ERR_TIMEOUT",
    "ERR_UNREACHABLE",
]
