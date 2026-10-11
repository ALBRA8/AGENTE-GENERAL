#!/usr/bin/env python3
"""09_audit_tamper_detection.py — Append audit entries, mutate one, verify detection.

Demonstrates the tamper-evident audit log:
    1. Append 5 entries to a TamperEvidentAuditLog.
    2. verify_chain() returns valid=True, chain_length=5.
    3. Mutate a historical entry's `action` field.
    4. verify_chain() now returns valid=False; broken_at=mutated index.
    5. detect_tampering() lists every broken link.

Run:
    python examples/09_audit_tamper_detection.py
"""

from __future__ import annotations

from albra_seed.audit.tamper_evident import TamperEvidentAuditLog


def main() -> None:
    log = TamperEvidentAuditLog()

    # 1. Append 5 entries.
    for i in range(5):
        h = log.append({"action": f"step-{i}", "actor": "agent-1"})
        print(f"appended step-{i}  : hash={h[:16]}...")

    # 2. Verify the chain is intact.
    result = log.verify_chain()
    print(f"\nbefore tamper      : valid={result.valid} len={result.chain_length}")

    # 3. Mutate a historical entry.
    log.all()[2].action = "TAMPERED"
    print(f"mutated entry[2]   : action={log.all()[2].action!r}")

    # 4. Verify the chain detects the tampering.
    after = log.verify_chain()
    print(f"\nafter tamper       : valid={after.valid}")
    print(f"                   : broken_at={after.broken_at}")
    print(f"                   : kind={after.kind}")
    print(f"                   : expected={after.expected_hash[:16] if after.expected_hash else None}...")
    print(f"                   : actual  ={after.actual_hash[:16] if after.actual_hash else None}...")

    # 5. List every tamper evidence entry.
    evidences = log.detect_tampering()
    print(f"\ndetect_tampering() : {len(evidences)} broken link(s)")
    for ev in evidences:
        print(f"  - index={ev.index} kind={ev.kind} entry_id={ev.entry_id}")


if __name__ == "__main__":
    main()
