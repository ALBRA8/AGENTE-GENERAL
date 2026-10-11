"""`albra agent info` — show agent manifest."""

from __future__ import annotations

import argparse
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "agent",
        help="agent manifest commands",
        description="Inspect the registered agent's manifest.",
    )
    sub = p.add_subparsers(dest="agent_cmd", metavar="<subcommand>")
    info = sub.add_parser("info", help="show agent manifest (default)")
    info.set_defaults(func=run)
    p.set_defaults(func=run, agent_cmd="info")


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    agent = ctx.default_agent
    if agent is None:
        # Fall back to runtime-level identity.
        snap = {
            "agent_id": "seed",
            "name": "albra-seed",
            "version": "1.0.0",
            "seed_version": ctx.runtime.config.seed_version,
            "runtime_version": "1.0.0",
            "note": "no agent registered; showing seed identity",
        }
        fmt.render_kv(snap, title="agent info (seed)")
        return 0
    manifest = agent.identity.to_manifest()
    fmt.render_kv(manifest.summary(), title="agent info")
    return 0


__all__ = ["register", "run"]
