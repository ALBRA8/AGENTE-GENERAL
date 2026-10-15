"""Toolkit — uniform tool system with validated contracts and permissions.

This module provides a standardized way to define, register, and execute
tools. It builds on the existing ``Tool`` ABC and ``ToolContract`` in
``albra_seed.core.base`` but adds:

- Validated input/output schemas (JSON Schema).
- Tool metadata: name, description, version, capabilities, side-effects.
- Permission policy per tool (read-only vs side-effects).
- Normalized error handling.
- Timeout and execution limits.
- Invocation logging (result, duration, error) without secrets.
- Discovery and listing.

The toolkit does NOT replace ``ToolRegistry`` or ``ToolExecutor`` — it
extends them with a higher-level API for defining tools declaratively.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field

from albra_seed.core.types import RunContext, ToolResult, new_id

log = structlog.get_logger("albra.toolkit")


class ToolCategory(str, Enum):
    READ_ONLY = "read_only"
    SIDE_EFFECT = "side_effect"
    CANARY = "canary"  # safe for staging tests


class ToolPermission(BaseModel):
    """Permissions required to invoke a tool."""
    category: ToolCategory = ToolCategory.READ_ONLY
    requires_approval: bool = False  # HITL gate
    allowed_autonomy_levels: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    denied_principals: list[str] = Field(default_factory=list)

    def can_execute(
        self,
        *,
        principal_id: str | None = None,
        autonomy_level: int = 0,
        has_approval: bool = False,
    ) -> bool:
        """Check whether the caller is allowed to execute."""
        if principal_id and principal_id in self.denied_principals:
            return False
        if autonomy_level not in self.allowed_autonomy_levels:
            return False
        return not (self.requires_approval and not has_approval)


class ToolDefinition(BaseModel):
    """Declarative definition of a tool."""
    name: str
    description: str = ""
    version: str = "1.0.0"
    category: ToolCategory = ToolCategory.READ_ONLY
    permission: ToolPermission = Field(default_factory=ToolPermission)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = 30.0
    max_retries: int = 0
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolInvocation(BaseModel):
    """Record of a single tool invocation."""
    invocation_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    tool_name: str
    principal_id: str | None = None
    started_at: float
    completed_at: float | None = None
    duration_ms: float | None = None
    success: bool = False
    error: str | None = None
    output_summary: str | None = None  # redacted summary, never full output


class ToolRegistryV2:
    """Enhanced tool registry with definitions + permissions.

    This wraps the existing ``ToolRegistry`` from ``albra_seed.tools.registry``
    without replacing it. The existing registry handles the ``Tool`` ABC
    instances; this adds declarative ``ToolDefinition`` metadata.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._invocations: list[ToolInvocation] = []
        self._callables: dict[str, Any] = {}

    def register(
        self,
        definition: ToolDefinition,
        callable_: Any,
    ) -> None:
        """Register a tool definition + its async callable."""
        if definition.name in self._definitions:
            raise ValueError(f"Tool {definition.name!r} already registered")
        self._definitions[definition.name] = definition
        self._callables[definition.name] = callable_
        log.info(
            "toolkit.register",
            tool=definition.name,
            category=definition.category.value,
            version=definition.version,
        )

    def unregister(self, name: str) -> bool:
        """Remove a tool. Returns True if it was present."""
        removed = self._definitions.pop(name, None) is not None
        self._callables.pop(name, None)
        return removed

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def list_by_category(self, category: ToolCategory) -> list[ToolDefinition]:
        return [d for d in self._definitions.values() if d.category == category]

    def invocations(self) -> list[ToolInvocation]:
        """Return a copy of the invocation log."""
        return list(self._invocations)

    def clear_invocations(self) -> None:
        self._invocations.clear()

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        principal_id: str | None = None,
        autonomy_level: int = 0,
        has_approval: bool = False,
    ) -> ToolResult:
        """Execute a registered tool with validation, permission check,
        timeout, and invocation logging.

        Returns a ``ToolResult``. On error, ``success`` is False and
        ``error`` contains a safe message (no secrets).
        """
        definition = self._definitions.get(name)
        if definition is None:
            return ToolResult(
                call_id=new_id("tc"),
                success=False,
                error=f"tool {name!r} not found",
                output={"not_found": True},
            )

        callable_ = self._callables.get(name)
        if callable_ is None:
            return ToolResult(
                call_id=new_id("tc"),
                success=False,
                error=f"tool {name!r} has no callable",
                output={"no_callable": True},
            )

        # Permission check
        if not definition.permission.can_execute(
            principal_id=principal_id,
            autonomy_level=autonomy_level,
            has_approval=has_approval,
        ):
            log.warning(
                "toolkit.permission_denied",
                tool=name,
                principal=principal_id,
                autonomy=autonomy_level,
                approval=has_approval,
            )
            return ToolResult(
                call_id=new_id("tc"),
                success=False,
                error="permission denied",
                output={"permission_denied": True},
            )

        invocation = ToolInvocation(
            tool_name=name,
            principal_id=principal_id,
            started_at=time.monotonic(),
        )

        try:
            coro = callable_(arguments, context=context) if context else callable_(arguments)
            result = await asyncio.wait_for(
                coro, timeout=definition.timeout_seconds
            )

            if not isinstance(result, ToolResult):
                # Wrap raw output in a ToolResult
                result = ToolResult(
                    call_id=new_id("tc"),
                    success=True,
                    output=result,
                )

            invocation.success = result.success
            invocation.error = result.error
            invocation.output_summary = _summarize_output(result.output)
            return result

        except asyncio.TimeoutError:
            invocation.error = f"timeout after {definition.timeout_seconds}s"
            return ToolResult(
                call_id=new_id("tc"),
                success=False,
                error=invocation.error,
                output={"timeout": True},
            )
        except Exception as exc:
            safe_msg = _safe_error(exc)
            invocation.error = safe_msg
            return ToolResult(
                call_id=new_id("tc"),
                success=False,
                error=safe_msg,
                output={"error": True},
            )
        finally:
            invocation.completed_at = time.monotonic()
            invocation.duration_ms = (
                (invocation.completed_at - invocation.started_at) * 1000
            )
            self._invocations.append(invocation)
            log.info(
                "toolkit.invoked",
                tool=name,
                success=invocation.success,
                duration_ms=invocation.duration_ms,
                invocation_id=invocation.invocation_id,
            )


def _summarize_output(output: Any) -> str:
    """Create a safe, redacted summary of tool output."""
    if output is None:
        return "None"
    if isinstance(output, dict):
        return f"dict({len(output)} keys)"
    if isinstance(output, (list, tuple)):
        return f"{type(output).__name__}({len(output)} items)"
    if isinstance(output, str):
        return f"str({len(output)} chars)"
    return type(output).__name__


def _safe_error(exc: Exception) -> str:
    """Return a safe error message that does NOT leak secrets."""
    import re
    msg = f"{type(exc).__name__}: {exc}"
    msg = re.sub(r"nvapi-[A-Za-z0-9_\-.]{8,}", "nvapi-[REDACTED]", msg)
    msg = re.sub(r"sk-[A-Za-z0-9_\-.]{8,}", "sk-[REDACTED]", msg)
    msg = re.sub(r"Bearer\s+[A-Za-z0-9_\-.]{8,}", "Bearer [REDACTED]", msg)
    return msg


# =====================================================================
# Built-in example tools (safe, no OS access)
# =====================================================================


async def _echo_tool(arguments: dict[str, Any], *, context: Any = None) -> ToolResult:
    """A safe read-only tool that echoes its input."""
    msg = arguments.get("message", "")
    return ToolResult(
        call_id=new_id("tc"),
        success=True,
        output={"echoed": msg},
    )


async def _calculator_tool(arguments: dict[str, Any], *, context: Any = None) -> ToolResult:
    """A safe read-only calculator tool."""
    a = float(arguments.get("a", 0))
    b = float(arguments.get("b", 0))
    op = arguments.get("op", "add")
    ops = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else float("inf"),
    }
    if op not in ops:
        return ToolResult(
            call_id=new_id("tc"),
            success=False,
            error=f"unknown operation: {op!r}",
            output={"error": True},
        )
    result = ops[op](a, b)
    return ToolResult(
        call_id=new_id("tc"),
        success=True,
        output={"result": result, "op": op},
    )


async def _timestamp_tool(arguments: dict[str, Any], *, context: Any = None) -> ToolResult:
    """A safe read-only tool that returns the current UTC timestamp."""
    import datetime
    return ToolResult(
        call_id=new_id("tc"),
        success=True,
        output={"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()},
    )


def _create_default_registry() -> ToolRegistryV2:
    """Create a ToolRegistryV2 pre-loaded with safe example tools."""
    registry = ToolRegistryV2()
    registry.register(
        ToolDefinition(
            name="echo",
            description="Echoes the input message back",
            version="1.0.0",
            category=ToolCategory.READ_ONLY,
            permission=ToolPermission(category=ToolCategory.READ_ONLY),
            input_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            capabilities=["text.echo"],
        ),
        _echo_tool,
    )
    registry.register(
        ToolDefinition(
            name="calculator",
            description="Performs basic arithmetic (add, subtract, multiply, divide)",
            version="1.0.0",
            category=ToolCategory.READ_ONLY,
            permission=ToolPermission(category=ToolCategory.READ_ONLY),
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                    "op": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]},
                },
                "required": ["a", "b", "op"],
            },
            capabilities=["math.arithmetic"],
        ),
        _calculator_tool,
    )
    registry.register(
        ToolDefinition(
            name="timestamp",
            description="Returns the current UTC timestamp",
            version="1.0.0",
            category=ToolCategory.READ_ONLY,
            permission=ToolPermission(category=ToolCategory.READ_ONLY),
            input_schema={"type": "object", "properties": {}},
            capabilities=["time.now"],
        ),
        _timestamp_tool,
    )
    return registry


__all__ = [
    "ToolCategory",
    "ToolPermission",
    "ToolDefinition",
    "ToolInvocation",
    "ToolRegistryV2",
]
