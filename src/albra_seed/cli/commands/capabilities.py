"""`albra capabilities` — list capabilities."""

from __future__ import annotations

import argparse
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "capabilities",
        help="list registered capabilities",
        description="List capabilities from the runtime's capability registry.",
    )
    p.add_argument(
        "--disabled",
        action="store_true",
        help="also list disabled capabilities",
    )
    p.set_defaults(func=run)


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    cap_registry = ctx.runtime.get_component("capability_registry")
    if cap_registry is None:
        # Fall back to identity-declared capabilities.
        agent = ctx.default_agent
        caps = list(agent.identity.capabilities) if agent else []
        fmt.render_table(
            [{"name": c, "enabled": True} for c in caps],
            title="capabilities (from identity)",
        )
        return 0
    rows = []
    if hasattr(cap_registry, "snapshot"):
        for row in cap_registry.snapshot():
            if not args.disabled and not row.get("enabled", True):
                continue
            rows.append(row)
    fmt.render_table(rows, title="capabilities")
    return 0


__all__ = ["register", "run"]
