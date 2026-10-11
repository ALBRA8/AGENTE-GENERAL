"""`albra health` — runtime health snapshot."""

from __future__ import annotations

import argparse
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "health",
        help="show runtime health (status, phase, components, sessions)",
        description="Show the runtime health snapshot.",
    )
    p.set_defaults(func=run)


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    snap = ctx.runtime.health()
    fmt.render_kv(snap, title="runtime health")
    return 0


__all__ = ["register", "run"]
