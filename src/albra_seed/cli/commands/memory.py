"""`albra memory` — show memory stats (count by domain)."""

from __future__ import annotations

import argparse
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "memory",
        help="show memory stats",
        description="Show counts of records per memory domain.",
    )
    p.add_argument(
        "--domain",
        default=None,
        help="filter to a single memory domain",
    )
    p.set_defaults(func=run)


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    rows: list[dict[str, Any]] = []
    # ctx.runtime exposes its internal _memory_stores map directly; for
    # CORE_ONLY mode this is empty, so the output will simply show no
    # rows.
    domains = list(ctx.runtime._memory_stores.keys())  # noqa: SLF001
    if args.domain:
        domains = [d for d in domains if d == args.domain]
    for d in domains:
        store = ctx.runtime.get_memory_store(d)
        count = -1
        try:
            if hasattr(store, "count"):
                # Async stores expose `count()` returning a coroutine.
                import asyncio
                maybe = store.count()
                if asyncio.iscoroutine(maybe):
                    count = asyncio.run(maybe)
                else:
                    count = int(maybe)
            elif hasattr(store, "all"):
                maybe = store.all()
                if asyncio.iscoroutine(maybe):
                    count = len(asyncio.run(maybe))
                else:
                    count = len(maybe)
        except Exception:
            count = -1
        rows.append({"domain": d, "records": count})
    if not rows:
        fmt.render_kv({"memory": [], "note": "no memory stores registered"})
        return 0
    fmt.render_table(rows, title="memory stats")
    return 0


__all__ = ["register", "run"]
