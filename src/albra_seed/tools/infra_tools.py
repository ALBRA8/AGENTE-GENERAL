"""Universal infra tools shipped with the SEED.

NO domain tools here (no legal/financial/sales/marketing tools). Only
generic infrastructure: echo, health-check, no-op, timestamp, status.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from albra_seed.core.base import Tool, ToolContract
from albra_seed.core.types import RiskLevel, RunContext, ToolResult, new_id, utc_now


# =====================================================================
# EchoTool
# =====================================================================


class EchoTool(Tool):
    """Echoes input — for testing and connectivity checks."""

    def __init__(self) -> None:
        self.contract = ToolContract(
            name="echo",
            description="Echo the provided message back to the caller.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "required": ["message"],
                "properties": {
                    "message": {"type": "string"},
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "echoed": {"type": "string"},
                    "received_at": {"type": "string"},
                },
                "required": ["echoed", "received_at"],
            },
            risk_level=RiskLevel.LOW,
            timeout_seconds=5,
            is_dry_run_capable=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        msg = arguments.get("message", "")
        if dry_run:
            return ToolResult(
                call_id=new_id("tc"),
                success=True,
                output={"echoed": msg, "received_at": utc_now().isoformat(), "dry_run": True},
            )
        return ToolResult(
            call_id=new_id("tc"),
            success=True,
            output={"echoed": msg, "received_at": utc_now().isoformat()},
        )


# =====================================================================
# HealthCheckTool
# =====================================================================


class HealthCheckTool(Tool):
    """Returns the health state of a named component (by simple lookup)."""

    def __init__(self) -> None:
        self.contract = ToolContract(
            name="health_check",
            description="Return the health status of a named component.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "required": ["component"],
                "properties": {"component": {"type": "string"}},
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "component": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["component", "status"],
            },
            risk_level=RiskLevel.LOW,
            timeout_seconds=5,
            is_dry_run_capable=True,
        )
        self._components: dict[str, str] = {}

    def register_component(self, name: str, status: str = "healthy") -> None:
        self._components[name] = status

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        component = arguments.get("component", "")
        status = self._components.get(component, "not_configured")
        return ToolResult(
            call_id=new_id("tc"),
            success=True,
            output={"component": component, "status": status, "dry_run": dry_run},
        )


# =====================================================================
# NoOpTool
# =====================================================================


class NoOpTool(Tool):
    """Does nothing, returns success. Useful for placeholder slots."""

    def __init__(self) -> None:
        self.contract = ToolContract(
            name="noop",
            description="Do nothing and return success.",
            version="1.0.0",
            input_schema={"type": "object", "additionalProperties": True},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
            risk_level=RiskLevel.LOW,
            timeout_seconds=2,
            is_dry_run_capable=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        return ToolResult(call_id=new_id("tc"), success=True, output={"ok": True, "dry_run": dry_run})


# =====================================================================
# TimestampTool
# =====================================================================


class TimestampTool(Tool):
    """Returns the current UTC timestamp."""

    def __init__(self) -> None:
        self.contract = ToolContract(
            name="timestamp",
            description="Return the current UTC timestamp in ISO-8601 format.",
            version="1.0.0",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={"type": "object", "properties": {"utc": {"type": "string"}}, "required": ["utc"]},
            risk_level=RiskLevel.LOW,
            timeout_seconds=2,
            is_dry_run_capable=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        now: datetime = utc_now()
        return ToolResult(
            call_id=new_id("tc"),
            success=True,
            output={"utc": now.isoformat(), "epoch": int(now.timestamp()), "dry_run": dry_run},
        )


# =====================================================================
# StatusTool
# =====================================================================


class StatusTool(Tool):
    """Returns agent-level status snapshot. Takes no domain knowledge."""

    def __init__(self) -> None:
        self.contract = ToolContract(
            name="status",
            description="Return a generic agent status snapshot.",
            version="1.0.0",
            input_schema={"type": "object", "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "version": {"type": "string"},
                },
                "required": ["status", "version"],
            },
            risk_level=RiskLevel.LOW,
            timeout_seconds=2,
            is_dry_run_capable=True,
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        return ToolResult(
            call_id=new_id("tc"),
            success=True,
            output={
                "status": "ready",
                "version": "1.0.0",
                "agent_id": context.agent_id if context else None,
                "execution_id": context.execution_id if context else None,
                "dry_run": dry_run,
            },
        )


# =====================================================================
# Convenience factory
# =====================================================================


def default_infra_tools() -> list[Tool]:
    """Return the canonical list of seed-shipped infra tools."""
    return [EchoTool(), HealthCheckTool(), NoOpTool(), TimestampTool(), StatusTool()]
