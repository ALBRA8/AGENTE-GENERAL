"""Genetic Parity Test subpackage.

This subpackage implements the ALBRA **Genetic Parity Test** — a formal
contract that verifies a specialized ALBRA agent preserves the universal
DNA of the Seed.

The seed ships a fixed catalog of universal components
(`UNIVERSAL_DNA_COMPONENTS`) that MUST be present in EVERY ALBRA agent,
whether specialized or not. A specialization MAY add components
(`ALLOWED_SPECIALIZATIONS`) but MUST NOT remove or replace any DNA
component. The `ParityChecker` exercises an installed ALBRA agent
(the Seed itself, or a specialization) and produces a `ParityReport`
that distinguishes:

  * UNIVERSAL DNA       — present / missing (every component must import)
  * BEHAVIORAL CHECKS   — runtime birth, agent loop, MemoryDV round-trip,
                          audit append, evidence register, firewall
                          default-deny, kill switch, doctor diagnose,
                          feedback record, learning propose, decision
                          memory, skill evolution.
  * SPECIALIZATION      — extra modules detected (any module in
                          ``sys.modules`` whose dotted name starts with
                          ``specialization.`` and is NOT in
                          ``ALLOWED_SPECIALIZATIONS``).
  * PURITY              — True iff all DNA present AND no extra
                          specialization components.

Public API:
    from albra_seed.parity import (
        ParityChecker,
        ParityReport,
        UNIVERSAL_DNA_COMPONENTS,
        ALLOWED_SPECIALIZATIONS,
    )

The checker is purely additive — it never imports specialization
modules, never modifies the seed, never spawns processes. It runs in
CORE_ONLY mode and reuses the always-on Mock provider.
"""

from albra_seed.parity.checker import ParityChecker, ParityReport
from albra_seed.parity.dna import (
    ALLOWED_SPECIALIZATIONS,
    UNIVERSAL_DNA_COMPONENTS,
)

__all__ = [
    "ParityChecker",
    "ParityReport",
    "UNIVERSAL_DNA_COMPONENTS",
    "ALLOWED_SPECIALIZATIONS",
]
