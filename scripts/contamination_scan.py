#!/usr/bin/env python3
"""contamination_scan.py — Run the ALBRA Genetic Contamination Test
against the current project's ``src/albra_seed/`` directory and print
a Markdown report.

The Genetic Contamination Test verifies that the universal Seed has
NOT been polluted by domain knowledge from any ALBRA specialization
(legal, CRM, trading, YouTube automation, SECOP radar, chismoso, …).
A specialization is built by cloning the Seed and ADDING domain code
on top — never by mutating the Seed itself. If a domain keyword
appears inside ``src/albra_seed/`` outside the explicitly-allowed
paths, that is a genetic contamination event.

Exit code:
    0 — PASS (no critical findings; ``is_clean == True``). Warnings
        do NOT fail the seed.
    1 — FAIL (at least one critical finding).

Usage:
    python scripts/contamination_scan.py
    python scripts/contamination_scan.py --project-root /path/to/agent-general
    python scripts/contamination_scan.py --json
"""

from __future__ import annotations

import argparse
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

from albra_seed.contamination import (  # noqa: E402  - sys.path bootstrapped above
    ContaminationScanner,
)

# ---------------------------------------------------------------------------
# Markdown report rendering
# ---------------------------------------------------------------------------


def _render_markdown(report_dict: dict[str, Any]) -> str:
    """Render a ContaminationReport.model_dump() result as Markdown."""
    lines: list[str] = []
    lines.append("# ALBRA Genetic Contamination Test Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Files scanned**: {report_dict['total_files_scanned']}")
    lines.append(f"- **Total findings**: {report_dict['total_findings']}")
    lines.append(f"- **Critical findings**: {report_dict['critical_findings']}")
    lines.append(f"- **Warning findings**: {report_dict['warning_findings']}")
    lines.append(f"- **is_clean**: `{report_dict['is_clean']}`")
    lines.append(f"- **Scanned at (UTC)**: {report_dict['scanned_at']}")
    lines.append("")

    findings = report_dict.get("findings") or []
    if findings:
        lines.append("## Findings")
        lines.append("")
        lines.append("| Severity | Type | Path | Line:Col | Keyword / Match | Context |")
        lines.append("|---|---|---|---|---|---|")
        for f in findings:
            ctx = (f["context"] or "").replace("|", "\\|")
            if len(ctx) > 80:
                ctx = ctx[:77] + "..."
            kw = (f["keyword"] or "").replace("|", "\\|")
            lines.append(
                f"| {f['severity']} | {f['finding_type']} | `{f['path']}` "
                f"| {f['line']}:{f['column']} | `{kw}` | {ctx} |"
            )
        lines.append("")

    if report_dict["is_clean"]:
        lines.append("## Verdict: PASS")
        lines.append("")
        lines.append("The Seed is free of domain contamination (zero critical "
                     "findings). Warnings, if any, indicate documentation "
                     "that may need review but are NOT a violation.")
    else:
        lines.append("## Verdict: FAIL")
        lines.append("")
        lines.append("The Seed is CONTAMINATED. Critical findings above show "
                     "domain-specific code or keywords that must be removed "
                     "from the universal Seed.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contamination_scan.py",
        description="Run the ALBRA Genetic Contamination Test on src/albra_seed/.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_PROJECT_ROOT,
        help="Project root containing src/albra_seed/ (default: this repo).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON instead of Markdown.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    scanner = ContaminationScanner(args.project_root)
    report = scanner.scan()
    d = report.model_dump(mode="json")

    if args.json:
        print(json.dumps(d, indent=2, default=str))
    else:
        print(_render_markdown(d))

    return 0 if report.is_clean else 1


if __name__ == "__main__":
    sys.exit(main())
