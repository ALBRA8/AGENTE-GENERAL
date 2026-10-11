"""`albra tools` — list registered tools."""

from __future__ import annotations

import argparse
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "tools",
        help="list registered tools",
        description="List tools known to the runtime.",
    )
    p.set_defaults(func=run)


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    # Prefer the ToolRegistry if registered as a component.
    tool_registry = ctx.runtime.get_component("tool_registry")
    rows: list[dict[str, Any]] = []
    if tool_registry is not None and hasattr(tool_registry, "list"):
        for name in tool_registry.list():
            rec = tool_registry.get(name)
            rows.append({
                "name": name,
                "availability": getattr(rec, "availability", None).value
                if rec and getattr(rec, "availability", None)
                else "unknown",
            })
    else:
        # Fall back to the runtime's direct _tools map.
        for name in list(ctx.runtime._tools.keys()):  # noqa: SLF001
            rows.append({"name": name, "availability": "available"})
    if not rows:
        fmt.render_kv({"tools": [], "note": "no tools registered"})
        return 0
    fmt.render_table(rows, title="tools")
    return 0


__all__ = ["register", "run"]
