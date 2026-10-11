"""CLI entry point — argparse-based main with subcommands.

NO hard deps on click/typer. CORE_ONLY mode imports cleanly.

Usage:
    albra health
    albra agent info
    albra doctor
    albra capabilities
    albra providers
    albra tools
    albra skills
    albra memory
    albra execution
    albra audit
    albra config

Each subcommand supports `--format {table,json,yaml}`.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Sequence

from albra_seed.cli.commands import (
    register_agent,
    register_audit,
    register_capabilities,
    register_config,
    register_doctor,
    register_execution,
    register_health,
    register_memory,
    register_providers,
    register_skills,
    register_tools,
)
from albra_seed.cli.context import CLIContext, make_default_context

PROG = "albra"
EPILOG = (
    "ALBRA AGENT SEED v1.0 — universal CLI. "
    "No domain commands. CORE_ONLY safe."
)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(prog=PROG, epilog=EPILOG)
    parser.add_argument(
        "--format",
        choices=["table", "json", "yaml"],
        default="table",
        help="output format (default: table)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print version and exit",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # Register every command group. Each `register` adds subparsers.
    # `ctx_factory` is invoked lazily only when the command runs.
    register_health(subparsers)
    register_agent(subparsers)
    register_doctor(subparsers)
    register_capabilities(subparsers)
    register_providers(subparsers)
    register_tools(subparsers)
    register_skills(subparsers)
    register_memory(subparsers)
    register_execution(subparsers)
    register_audit(subparsers)
    register_config(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        from albra_seed import __version__
        print(__version__)
        return 0

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    # Lazily build the context (Runtime + ConfigManager). This avoids
    # touching the runtime when `--help` is invoked.
    formatter = getattr(args, "format", "table")
    ctx: CLIContext = make_default_context(formatter=formatter)

    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 0
    try:
        result = handler(ctx, args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return int(result or 0)


def cli_entry() -> None:  # pragma: no cover - console_scripts glue
    sys.exit(main())


__all__ = ["main", "cli_entry", "build_parser"]
