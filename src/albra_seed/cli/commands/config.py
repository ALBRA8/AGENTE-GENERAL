"""`albra config` — show current configuration (SECRETS REDACTED)."""

from __future__ import annotations

import argparse
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "config",
        help="show current configuration (SECRETS REDACTED)",
        description="Print the current SeedConfig + AgentConfig + secrets map "
        "(with all secret values redacted).",
    )
    p.add_argument(
        "--validate",
        action="store_true",
        help="run ConfigValidator and print errors/warnings",
    )
    p.add_argument(
        "--save",
        default=None,
        help="write redacted config JSON to this path",
    )
    p.set_defaults(func=run)


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    if args.validate:
        result = ctx.config.validate()
        fmt.render(result.to_dict(), title="config validation")
        return 0 if result.ok else 1
    if args.save:
        path = ctx.config.save(args.save, include_secrets=False)
        fmt.render_kv({"saved": str(path), "include_secrets": False})
        return 0
    snap = ctx.config.redacted_snapshot()
    fmt.render(snap, title="current config (secrets redacted)")
    return 0


__all__ = ["register", "run"]
