#!/usr/bin/env python3
"""10_doctor_diagnose.py — Run the Doctor, print the diagnostic report.

Demonstrates the Doctor:
    1. Build a Doctor instance (with default check registry).
    2. Run diagnose() — runs all 10 built-in async checks in parallel.
    3. Print the overall status + per-component summary.
    4. Print the implementation status (IMPLEMENTED vs MOCK vs
       EXTERNAL_DEPENDENCY) — honest reporting.

Run:
    python examples/10_doctor_diagnose.py
"""

from __future__ import annotations

import asyncio

from albra_seed.doctor.doctor import Doctor


async def main() -> None:
    doctor = Doctor()

    # 1. Run diagnostics.
    report = await doctor.diagnose()

    # 2. Overall status.
    print(f"overall_status    : {report.overall_status.value}")
    print(f"component_count   : {len(report.components)}")
    print(f"summary           : {report.summary()}")

    # 3. Per-component breakdown.
    print("\nper-component:")
    print(f"  {'NAME':<24}{'STATUS':<16}{'IMPL':<22}{'DETAILS':<32}")
    print("  " + "-" * 90)
    for c in sorted(report.components, key=lambda x: x.name):
        print(f"  {c.name:<24}{c.status.value:<16}"
              f"{c.implementation_status.value:<22}{c.details[:30]:<32}")

    # 4. Show which components need attention.
    unhealthy = [c for c in report.components if c.status.value not in {"healthy", "warning"}]
    if unhealthy:
        print(f"\nattention needed  : {len(unhealthy)} component(s)")
        for c in unhealthy:
            print(f"  - {c.name}: {c.status.value} ({c.implementation_status.value})")
    else:
        print("\nall components healthy.")


if __name__ == "__main__":
    asyncio.run(main())
