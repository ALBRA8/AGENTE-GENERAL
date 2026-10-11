"""`albra audit` — show recent audit entries."""

from __future__ import annotations

import argparse
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "audit",
        help="show recent audit entries",
        description="Show recent entries from the runtime's audit log.",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="max entries to show (default: 20)",
    )
    p.add_argument(
        "--actor",
        default=None,
        help="filter by actor",
    )
    p.add_argument(
        "--action",
        default=None,
        help="filter by action",
    )
    p.set_defaults(func=run)


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    audit_logger = ctx.runtime.get_component("audit_logger")
    if audit_logger is None:
        fmt.render_kv({
            "audit": [],
            "note": "no audit logger registered with the runtime",
        })
        return 0
    # AuditLogger (NORMAL) exposes `.all()` and `.filter(...)`.
    if hasattr(audit_logger, "filter"):
        entries = audit_logger.filter(actor=args.actor, action=args.action)
    elif hasattr(audit_logger, "all"):
        entries = audit_logger.all()
    else:
        entries = []
    rows: list[dict[str, Any]] = []
    for e in entries[-args.limit:]:
        rows.append({
            "timestamp": str(getattr(e, "timestamp", "")),
            "action": getattr(e, "action", ""),
            "actor": getattr(e, "actor", ""),
            "tool": getattr(e, "tool", ""),
            "result": getattr(e, "result", ""),
            "correlation_id": getattr(e, "correlation_id", ""),
        })
    fmt.render_table(rows, title=f"audit log ({len(rows)})")
    return 0


__all__ = ["register", "run"]
