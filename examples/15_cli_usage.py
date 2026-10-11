#!/usr/bin/env python3
"""15_cli_usage.py — Programmatic CLI usage.

Demonstrates invoking the ALBRA CLI from Python (without spawning a
subprocess):
    1. `albra --version` → prints the seed version, exits 0.
    2. `albra health` → prints the runtime health snapshot, exits 0.
    3. `albra doctor` (alias: diagnostics) → runs the Doctor.
    4. `albra --format json config` → prints the (redacted) config as JSON.

The CLI is argparse-based, stdlib-only, and CORE_ONLY safe.

Run:
    python examples/15_cli_usage.py
"""

from __future__ import annotations

import sys

from albra_seed import main as cli_main


def run(args: list[str]) -> int:
    print(f"\n$ albra {' '.join(args)}")
    rc = cli_main(args)
    print(f"(exit code: {rc})")
    return rc


def main() -> None:
    # 1. Version.
    run(["--version"])

    # 2. Help (no command → print_help).
    run([])

    # 3. Health snapshot.
    run(["health"])

    # 4. Diagnostics (Doctor) — alias is `doctor`.
    run(["doctor"])

    # 5. Config in JSON (secrets are redacted automatically).
    run(["--format", "json", "config"])

    # 6. Capabilities (empty unless the runtime is built up further).
    run(["capabilities"])


if __name__ == "__main__":
    main()
    sys.exit(0)
