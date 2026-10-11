"""`albra skills` — list registered skills."""

from __future__ import annotations

import argparse
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "skills",
        help="list registered skills",
        description="List skills known to the runtime.",
    )
    p.set_defaults(func=run)


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    skill_registry = ctx.runtime.get_component("skill_registry")
    rows: list[dict[str, Any]] = []
    if skill_registry is not None and hasattr(skill_registry, "list"):
        for name in skill_registry.list():
            rec = getattr(skill_registry, "_skills", {}).get(name)
            rows.append({
                "name": name,
                "enabled": getattr(rec, "enabled", True) if rec else True,
            })
    else:
        # Fall back to the runtime's direct _skills map.
        for name in list(ctx.runtime._skills.keys()):  # noqa: SLF001
            rows.append({"name": name, "enabled": True})
    if not rows:
        fmt.render_kv({"skills": [], "note": "no skills registered"})
        return 0
    fmt.render_table(rows, title="skills")
    return 0


__all__ = ["register", "run"]
