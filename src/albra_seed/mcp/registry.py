"""MCP registry — registers servers + clients. Validates availability and
records every cross-process call in an audit log.

The registry is the single coordination point between MCP clients and
servers in a running ALBRA seed: specialisation layers register both
servers they expose and clients they consume from; the registry then
provides a single `call(server_name, capability, args)` entry point
that records an audit entry around every call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Awaitable

from albra_seed.core.types import utc_now
from albra_seed.mcp.client import MCPClient
from albra_seed.mcp.protocol import MCPResponse
from albra_seed.mcp.server import MCPServer
from albra_seed.mcp.validator import MCPValidator, ValidationResult

# Audit sink is an async callable (entry: dict) -> None.
AuditSink = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class AuditRecord:
    server_name: str
    capability: str
    request_id: str
    success: bool
    error: str | None
    duration_ms: float | None
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_name": self.server_name,
            "capability": self.capability,
            "request_id": self.request_id,
            "success": self.success,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
        }


class MCPRegistry:
    """Registry of named MCP servers + clients with audit recording."""

    def __init__(self, audit_sink: AuditSink | None = None) -> None:
        self._servers: dict[str, MCPServer] = {}
        self._clients: dict[str, MCPClient] = {}
        self._audit_sink = audit_sink
        self._records: list[AuditRecord] = []
        self._lock = asyncio.Lock()
        self._validator = MCPValidator()

    # -- registration ------------------------------------------------
    def register_server(self, name: str, server: MCPServer) -> None:
        if not isinstance(server, MCPServer):
            raise TypeError("server must be an MCPServer instance")
        self._servers[name] = server

    def register_client(self, name: str, client: MCPClient) -> None:
        if not isinstance(client, MCPClient):
            raise TypeError("client must be an MCPClient instance")
        self._clients[name] = client

    def unregister_server(self, name: str) -> bool:
        return self._servers.pop(name, None) is not None

    def unregister_client(self, name: str) -> bool:
        return self._clients.pop(name, None) is not None

    def get_server(self, name: str) -> MCPServer | None:
        return self._servers.get(name)

    def get_client(self, name: str) -> MCPClient | None:
        return self._clients.get(name)

    def list_servers(self) -> list[str]:
        return list(self._servers.keys())

    def list_clients(self) -> list[str]:
        return list(self._clients.keys())

    # -- validation --------------------------------------------------
    async def validate_server(self, name: str) -> ValidationResult:
        srv = self._servers.get(name)
        if srv is None:
            r = ValidationResult(reachable=False, healthy=False, error="not registered")
            return r
        return await self._validator.validate_server(srv)

    async def validate_client(self, name: str) -> ValidationResult:
        cli = self._clients.get(name)
        if cli is None:
            r = ValidationResult(reachable=False, healthy=False, error="not registered")
            return r
        return await self._validator.validate_client(cli)

    # -- audited call ------------------------------------------------
    async def call(
        self,
        server_name: str,
        capability: str,
        args: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> MCPResponse:
        """Invoke `capability` on the named server, recording an audit
        entry. Returns the raw MCPResponse (no raise on error)."""
        srv = self._servers.get(server_name)
        if srv is None:
            resp = MCPResponse.error_response(
                request_id="no-server",
                error=f"server not registered: {server_name}",
            )
            await self._record(
                AuditRecord(
                    server_name=server_name,
                    capability=capability,
                    request_id="no-server",
                    success=False,
                    error="server not registered",
                    duration_ms=None,
                )
            )
            return resp
        from albra_seed.mcp.protocol import MCPRequest

        request = MCPRequest(
            capability=capability,
            arguments=dict(args or {}),
            timeout_seconds=timeout_seconds,
        )
        started = utc_now()
        try:
            resp = await srv.handle_request(request)
        except Exception as exc:  # pragma: no cover - defensive
            resp = MCPResponse.error_response(
                request.request_id, f"registry error: {exc}"
            )
        duration_ms = (utc_now() - started).total_seconds() * 1000.0
        resp.duration_ms = resp.duration_ms or round(duration_ms, 3)
        await self._record(
            AuditRecord(
                server_name=server_name,
                capability=capability,
                request_id=request.request_id,
                success=resp.success,
                error=resp.error,
                duration_ms=resp.duration_ms,
            )
        )
        return resp

    async def _record(self, record: AuditRecord) -> None:
        async with self._lock:
            self._records.append(record)
        if self._audit_sink is not None:
            try:
                await self._audit_sink(record.to_dict())
            except Exception:
                # Audit failures must NEVER break the call path.
                pass

    def audit_records(self) -> list[AuditRecord]:
        return list(self._records)


__all__ = ["MCPRegistry", "AuditRecord", "AuditSink"]
