"""`albra providers` — list LLM providers and their status."""

from __future__ import annotations

import argparse
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "providers",
        help="list registered LLM providers",
        description="List LLM providers known to the runtime.",
    )
    p.set_defaults(func=run)


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    # The runtime has its own internal _providers dict; but the canonical
    # registry lives under COMPONENT_PROVIDER_ROUTER (ProviderRegistry).
    router = ctx.runtime.get_component("provider_router")
    rows: list[dict[str, Any]] = []
    if router is not None and hasattr(router, "registry"):
        registry = router.registry
        for name in registry.list_all():
            health = registry.health_for(name)
            rows.append({
                "name": name,
                "state": health.state.value if health else "unknown",
                "available": health.is_available if health else False,
                "is_mock": getattr(registry.get(name), "is_mock", False),
                "is_external": getattr(registry.get(name), "is_external", False),
            })
    else:
        # Fall back to the runtime's direct _providers map (only present
        # if a specialization layer registered providers directly).
        for name in list(ctx.runtime._providers.keys()):  # noqa: SLF001
            prov = ctx.runtime._providers[name]  # noqa: SLF001
            rows.append({
                "name": name,
                "state": "unknown",
                "available": True,
                "is_mock": getattr(prov, "is_mock", False),
                "is_external": getattr(prov, "is_external", False),
            })
    if not rows:
        fmt.render_kv({"providers": [], "note": "no providers registered"})
        return 0
    fmt.render_table(rows, title="providers")
    return 0


__all__ = ["register", "run"]
