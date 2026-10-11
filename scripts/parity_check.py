#!/usr/bin/env python3
"""parity_check.py — Run the ALBRA Genetic Parity Test against the
current Python environment and print a Markdown report.

The Genetic Parity Test verifies that an installed ALBRA agent (the
Seed itself, or a specialization) preserves the universal DNA of the
Seed. A specialization MAY add components (those in
``ALLOWED_SPECIALIZATIONS``) but MUST NOT remove or replace any
component in ``UNIVERSAL_DNA_COMPONENTS``.

Exit code:
    0 — PASS (all DNA present, all behavioral checks pass; is_pure_seed
        is True iff no extra specialization components detected).
    1 — FAIL (some DNA component missing OR some behavioral check
        failed). Extra specialization components alone do NOT cause
        a FAIL exit — they only flip ``is_pure_seed`` to False, which
        is reported but not treated as a parity failure (a
        specialization is allowed to add components).

Usage:
    python scripts/parity_check.py
    python scripts/parity_check.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Bootstrap sys.path so the script is runnable from any cwd.
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from albra_seed.parity import (  # noqa: E402  - sys.path bootstrapped above
    ALLOWED_SPECIALIZATIONS,
    UNIVERSAL_DNA_COMPONENTS,
    ParityChecker,
)


# ---------------------------------------------------------------------------
# Markdown report rendering
# ---------------------------------------------------------------------------


def _render_markdown(report_dict: dict[str, Any]) -> str:
    """Render a ParityReport.to_dict() result as a Markdown document."""
    lines: list[str] = []
    lines.append("# ALBRA Genetic Parity Test Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total DNA components**: {report_dict['total_components']}")
    lines.append(f"- **Present**: {report_dict['present']}")
    lines.append(f"- **Missing**: {report_dict['missing']}")
    lines.append(f"- **Behavioral checks passed**: {report_dict['checks_passed']}")
    lines.append(f"- **Behavioral checks failed**: {report_dict['checks_failed']}")
    lines.append(f"- **Specialization components detected**: "
                 f"{len(report_dict['specialization_components'])}")
    lines.append(f"- **is_pure_seed**: `{report_dict['is_pure_seed']}`")
    lines.append("")

    if report_dict.get("missing_components"):
        lines.append("## Missing DNA Components")
        lines.append("")
        for c in report_dict["missing_components"]:
            lines.append(f"- `albra_seed.{c}`")
        lines.append("")

    if report_dict["specialization_components"]:
        lines.append("## Extra Specialization Components")
        lines.append("")
        lines.append("These are not in `ALLOWED_SPECIALIZATIONS` — they cause "
                     "`is_pure_seed` to be False.")
        lines.append("")
        for c in report_dict["specialization_components"]:
            lines.append(f"- `{c}`")
        lines.append("")

    lines.append("## Universal DNA Catalog")
    lines.append("")
    lines.append(f"Total: {len(UNIVERSAL_DNA_COMPONENTS)} components — every "
                 "ALBRA agent MUST carry these.")
    lines.append("")
    lines.append("```")
    for c in UNIVERSAL_DNA_COMPONENTS:
        lines.append(f"albra_seed.{c}")
    lines.append("```")
    lines.append("")

    lines.append("## Allowed Specializations")
    lines.append("")
    lines.append("A specialization MAY add these — they do NOT affect "
                 "`is_pure_seed`.")
    lines.append("")
    lines.append("```")
    for c in ALLOWED_SPECIALIZATIONS:
        lines.append(c)
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parity_check.py",
        description="Run the ALBRA Genetic Parity Test.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON instead of Markdown.",
    )
    parser.add_argument(
        "--root-package",
        default="albra_seed",
        help="Root package name (default: albra_seed).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    checker = ParityChecker(root_package=args.root_package)
    report = asyncio.run(checker.run_all())
    d = report.to_dict()

    if args.json:
        print(json.dumps(d, indent=2, default=str))
    else:
        print(_render_markdown(d))

    # FAIL exit only on actual parity failures (missing DNA or failed
    # behavioral checks). Extra specialization components alone do NOT
    # cause a FAIL exit — they only flip is_pure_seed to False (which
    # is reported but is not itself a parity violation: specializations
    # are explicitly allowed to add components).
    if d["missing"] > 0 or d["checks_failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
