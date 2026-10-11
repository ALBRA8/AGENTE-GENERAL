"""Domain keywords that MUST NOT appear in src/albra_seed/ as code/content.

These words identify the known ALBRA specializations. The universal Seed
must be free of them in code, contracts, prompts, and knowledge — but they
are allowed in:

  * Documentation that explains the specialization CONTRACT
    (``docs/SPECIALIZATION.md``, ``docs/ARCHITECTURE.md``, ``README.md`` …).
  * Test fixtures that test the contamination detector itself
    (``tests/contamination/``, ``tests/genetic/``).
  * The detector package itself (``src/albra_seed/contamination/``).
  * Seed source files whose module docstring mentions a domain name
    *only* to assert that the Seed does NOT carry that domain
    (e.g. ``src/albra_seed/__init__.py`` says "Specialized agents
    (legal, sales, research, trading, …) are built by cloning this seed"
    — that is documentation, not contamination). These specific files
    are listed in ``ALLOWED_PATHS`` below with an explanatory comment.

The keyword list, the code-pattern regexes, and the allowed-paths list
are exported for reuse by the ``ContaminationScanner`` and by the CLI
``scripts/contamination_scan.py``.

NO domain knowledge is encoded here — only the *catalog* of words that
identify domains. The detector itself remains universal.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Domain name patterns (case-insensitive whole-word match)
# ---------------------------------------------------------------------------

FORBIDDEN_DOMAIN_KEYWORDS: list[str] = [
    # Known ALBRA specializations (spec §1 sources)
    "legal", "lawyer", "abogado", "juridico", "juridica", "abogada",
    "crm", "customer_relationship",
    "krea",
    "leads", "lead_gen",
    "trading", "trader",
    "youtube", "yt_automation",
    "secop", "secop2", "radar_secop",
    "chismoso",
    # Domain-specific concepts that shouldn't appear in universal seed
    "contract_drafting",
    "sales_pipeline",
    "marketing_campaign",
    "financial_advice",
    "investment_recommendation",
    # Add more as needed
]

# ---------------------------------------------------------------------------
# File paths where forbidden keywords ARE allowed (relative to project root)
# ---------------------------------------------------------------------------

ALLOWED_PATHS: list[str] = [
    # Documentation that legitimately mentions specialization names.
    "docs/SPECIALIZATION.md",          # explains specialization contract
    "docs/EXTENDING.md",               # may give examples
    "docs/ARCHITECTURE.md",            # may reference specialization
    "docs/UNIVERSAL_DNA_GAP_CLOSURE.md",  # this report itself
    "docs/AUDIT_MATRIX.md",
    "README.md",                       # may reference specialization
    # The contamination detector itself (this package + scanner + tests).
    "src/albra_seed/contamination/",   # the detector package
    "tests/contamination/",            # the contamination tests
    "tests/genetic/",                  # the parity tests (may mention)
    # CLI scripts that legitimately mention domain names.
    "scripts/contamination_scan.py",   # the scanner script
    "scripts/audit_matrix.py",          # may mention domains
    # ----- Seed files whose docstring mentions a domain name ONLY to
    # ----- assert that the Seed does NOT carry that domain. These are
    # ----- legitimate documentation embedded in source files, NOT
    # ----- contamination. The scanner skips them.
    "src/albra_seed/__init__.py",       # "Specialized agents (legal, ...)"
    "src/albra_seed/parity/dna.py",     # "Every specialized agent (legal, ...)"
    "src/albra_seed/tools/infra_tools.py",  # "no legal/financial/... tools"
]

# ---------------------------------------------------------------------------
# File extensions to scan
# ---------------------------------------------------------------------------

SCANNABLE_EXTENSIONS: set[str] = {".py", ".txt", ".md", ".yaml", ".yml", ".json", ".toml"}

# ---------------------------------------------------------------------------
# Specific code patterns that indicate contamination (regex)
# ---------------------------------------------------------------------------

FORBIDDEN_CODE_PATTERNS: list[str] = [
    # Imports of domain-specific packages (any domain prefix would be a
    # genetic contamination: the Seed must not import a specialization).
    r"import\s+(legal|crm|trading|youtube|secop|chismoso)",
    r"from\s+(legal|crm|trading|youtube|secop|chismoso)\s+import",
    # Hardcoded domain configurations.
    r'domain\s*=\s*["\'](?:legal|crm|trading|youtube|secop|chismoso)["\']',
    # Domain-specific tool/skill/agent class names — the universal Seed
    # carries only generic classes (Runtime, Agent, Tool, …).
    r"class\s+(Legal|CRM|Trading|YouTube|Secop|Chismoso)\w*",
]
