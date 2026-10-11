"""`albra doctor` / `albra diagnostics` — run diagnostic checks."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from albra_seed.cli.context import CLIContext
from albra_seed.cli.formatter import Formatter


def register(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "doctor",
        aliases=["diagnostics"],
        help="run diagnostic checks (Doctor)",
        description="Run every registered Doctor check and print the report.",
    )
    p.add_argument(
        "--component",
        default=None,
        help="filter to a single component name",
    )
    p.set_defaults(func=run)


def run(ctx: CLIContext, args: argparse.Namespace) -> int:
    fmt = Formatter(ctx.formatter_name)
    from albra_seed.doctor import Doctor  # lazy
    doctor = Doctor()
    try:
        report = asyncio.run(doctor.diagnose())
    except RuntimeError:
        # Already inside an event loop — fall back to building the report
        # manually from the registry.
        loop = asyncio.get_event_loop()
        results = loop.run_until_complete(doctor.registry.run_all())
        report = doctor._matrix_overlay(results)  # type: ignore[attr-defined]
    components = [c.to_dict() for c in report.components]
    if args.component:
        components = [c for c in components if c["name"] == args.component]
    payload = {
        "generated_at": report.generated_at,
        "overall_status": report.overall_status.value,
        "summary": report.summary(),
        "components": components,
    }
    fmt.render(payload, title="diagnostics report")
    return 0


__all__ = ["register", "run"]
