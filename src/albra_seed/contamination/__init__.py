"""Genetic Contamination Test subpackage.

This subpackage implements the ALBRA **Genetic Contamination Test** — a
formal contract that verifies the universal Seed has NOT been polluted
by domain knowledge from any ALBRA specialization (legal, CRM, trading,
YouTube automation, SECOP radar, chismoso, ...).

The Seed is universal DNA: it MUST carry no specialization-specific
keywords, imports, class names, or hardcoded domain configurations. A
specialization is built by cloning the Seed and ADDING domain code on
top — never by mutating the Seed itself. If a domain keyword appears
inside ``src/albra_seed/``, that is a *genetic contamination* event and
must be flagged.

Public API:
    from albra_seed.contamination import (
        ContaminationScanner,
        ContaminationReport,
        ContaminationFinding,
        FORBIDDEN_DOMAIN_KEYWORDS,
        FORBIDDEN_CODE_PATTERNS,
        ALLOWED_PATHS,
        SCANNABLE_EXTENSIONS,
    )

The scanner is purely additive — it never modifies the seed, never
imports specialization modules, never spawns processes. It walks the
filesystem using ``pathlib`` only (no external deps) and reads files
as UTF-8 text with ``errors='replace'`` so binary blobs never crash it.

Contamination findings are classified by severity:
    * ``critical`` — forbidden keyword in a ``.py`` file, or any
      forbidden code pattern (import / class-name / domain= assignment).
      These are real contamination events.
    * ``warning`` — forbidden keyword in a non-Python scannable file
      (``.md``, ``.txt``, ``.yaml``, ``.json``, ``.toml``) outside the
      ALLOWED_PATHS list. These MIGHT be contamination but are
      typically documentation that needs review.

``ContaminationReport.is_clean`` is True iff there are zero critical
findings (warnings do NOT fail the seed).
"""

from albra_seed.contamination.domains import (
    ALLOWED_PATHS,
    FORBIDDEN_CODE_PATTERNS,
    FORBIDDEN_DOMAIN_KEYWORDS,
    SCANNABLE_EXTENSIONS,
)
from albra_seed.contamination.scanner import (
    ContaminationFinding,
    ContaminationReport,
    ContaminationScanner,
)

__all__ = [
    "ContaminationScanner",
    "ContaminationReport",
    "ContaminationFinding",
    "FORBIDDEN_DOMAIN_KEYWORDS",
    "FORBIDDEN_CODE_PATTERNS",
    "ALLOWED_PATHS",
    "SCANNABLE_EXTENSIONS",
]
