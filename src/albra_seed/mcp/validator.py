"""MCP validator — validates that a server/client is reachable and responds
correctly to a discovery + echo-style probe."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from albra_seed.core.types import utc_now
from albra_seed.mcp.client import MCPClient
from albra_seed.mcp.protocol import MCPRequest
from albra_seed.mcp.server import MCPServer


@dataclass
class ValidationResult:
    """Outcome of an MCP validation probe."""

    reachable: bool
    healthy: bool
    capabilities_seen: list[str] = field(default_factory=list)
    error: str | None = None
    checked_at: str = field(default_factory=lambda: utc_now().isoformat())

    @property
    def ok(self) -> bool:
        return self.reachable and self.healthy and self.error is None


class MCPValidator:
    """Validates an MCP server or client.

    A server is validated by issuing a discovery + a no-op probe request
    against a synthetic capability (no side effects). A client is validated
    by calling its `health_check` and `discover` methods."""

    def __init__(self, probe_capability: str = "__mcp_probe__") -> None:
        # The probe capability is a well-known no-op name. Servers are not
        # required to register it; if absent we still report reachable=True.
        self._probe = probe_capability

    # -- server-side ------------------------------------------------
    async def validate_server(self, server: MCPServer) -> ValidationResult:
        try:
            healthy = await server.health()
        except Exception as exc:
            return ValidationResult(
                reachable=False, healthy=False, error=f"health error: {exc}"
            )
        try:
            caps = server.list_capabilities()
        except Exception as exc:
            return ValidationResult(
                reachable=healthy,
                healthy=False,
                error=f"list error: {exc}",
            )
        names = [c.name for c in caps]
        # Probe a registered capability if any (first one) to confirm dispatch
        # works end-to-end. Fall back to listing only when none registered.
        if names:
            try:
                req = MCPRequest(capability=names[0], arguments={})
                resp = await server.handle_request(req)
                if not resp.success:
                    return ValidationResult(
                        reachable=True,
                        healthy=False,
                        capabilities_seen=names,
                        error=f"probe failed: {resp.error}",
                    )
            except Exception as exc:
                return ValidationResult(
                    reachable=True,
                    healthy=False,
                    capabilities_seen=names,
                    error=f"probe error: {exc}",
                )
        return ValidationResult(
            reachable=True, healthy=healthy, capabilities_seen=names
        )

    # -- client-side ------------------------------------------------
    async def validate_client(self, client: MCPClient) -> ValidationResult:
        try:
            healthy = await client.health_check()
        except Exception as exc:
            return ValidationResult(
                reachable=False, healthy=False, error=f"health error: {exc}"
            )
        if not healthy:
            return ValidationResult(
                reachable=False, healthy=False, error="health_check returned False"
            )
        try:
            caps = await client.discover()
        except Exception as exc:
            return ValidationResult(
                reachable=True, healthy=False, error=f"discover error: {exc}"
            )
        return ValidationResult(
            reachable=True,
            healthy=True,
            capabilities_seen=[c.name for c in caps],
        )

    async def validate(
        self, target: MCPServer | MCPClient
    ) -> ValidationResult:
        if isinstance(target, MCPServer):
            return await self.validate_server(target)
        if isinstance(target, MCPClient):
            return await self.validate_client(target)
        return ValidationResult(
            reachable=False,
            healthy=False,
            error=f"unsupported target type: {type(target).__name__}",
        )


__all__ = ["MCPValidator", "ValidationResult"]
