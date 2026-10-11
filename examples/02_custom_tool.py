#!/usr/bin/env python3
"""02_custom_tool.py — Define + register a custom tool, run agent that uses it.

Demonstrates:
    1. Subclassing `Tool` and providing a `ToolContract`.
    2. Registering the tool with the Runtime.
    3. Registering the tool with the `ToolRegistry` component too.
    4. Invoking the tool directly via the registry (without an LLM round-trip).

The tool here is intentionally generic (NO domain knowledge): it reverses a
string. Specialization layers swap in their own domain tools the same way.

Run:
    python examples/02_custom_tool.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from albra_seed import Runtime, SeedConfig, RiskLevel
from albra_seed.core.base import Tool, ToolContract, ToolResult
from albra_seed.core.types import RunContext, new_id
from albra_seed.providers.mock import MockProvider
from albra_seed.providers.registry import ProviderRegistry
from albra_seed.providers.router import ProviderRouter
from albra_seed.runtime.runtime import COMPONENT_PROVIDER_ROUTER, COMPONENT_TOOL_REGISTRY
from albra_seed.tools.registry import ToolRegistry


class ReverseStringTool(Tool):
    """Reverses the input string. Generic — no domain knowledge."""

    def __init__(self) -> None:
        self.contract = ToolContract(
            name="reverse_string",
            description="Reverse the provided string.",
            version="1.0.0",
            input_schema={"type": "object", "required": ["text"],
                          "properties": {"text": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"reversed": {"type": "string"}}},
            risk_level=RiskLevel.LOW,
            permissions=["tool.reverse_string.execute"],
            timeout_seconds=5,
            is_dry_run_capable=True,
            is_external=False,
        )

    async def execute(self, arguments: dict[str, Any], *,
                      context: RunContext | None = None,
                      dry_run: bool = False) -> ToolResult:
        text = arguments.get("text", "")
        if dry_run:
            return ToolResult(call_id=new_id("tc"), success=True,
                              output={"reversed": "[dry-run] " + text[::-1]})
        return ToolResult(call_id=new_id("tc"), success=True,
                          output={"reversed": text[::-1]})


async def main() -> None:
    runtime = Runtime(config=SeedConfig())
    await runtime.startup()

    # Wire provider router (CORE_ONLY fallback).
    reg = ProviderRegistry(); reg.register(MockProvider())
    runtime.register(COMPONENT_PROVIDER_ROUTER, ProviderRouter(registry=reg))

    # Wire tool registry + register our custom tool.
    tool_registry = ToolRegistry()
    tool_registry.register(ReverseStringTool())
    runtime.register(COMPONENT_TOOL_REGISTRY, tool_registry)
    runtime.register_tool(ReverseStringTool())

    # Invoke the tool directly (skipping the LLM).
    tool = tool_registry.get("reverse_string")
    result = await tool.execute({"text": "ALBRA"})
    print(f"reversed        : {result.output['reversed']}")

    # Try the dry-run path.
    dry = await tool.execute({"text": "semilla"}, dry_run=True)
    print(f"dry-run         : {dry.output['reversed']}")

    await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
