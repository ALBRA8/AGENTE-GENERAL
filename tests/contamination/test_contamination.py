"""Genetic Contamination Test — verifies the universal Seed has NOT been
polluted by domain knowledge from any ALBRA specialization
(Task ID GAP-7).

These tests exercise :class:`ContaminationScanner` against:

  * synthetic temp trees (positive + negative cases), and
  * the actual ALBRA Seed checkout at ``/home/z/my-project/agent-general``
    (the seed MUST be CLEAN — zero critical findings).

The scanner enforces the SEED's core invariant: a specialization is
built by cloning the Seed and ADDING domain code on top — never by
mutating the Seed itself. If a domain keyword (``legal``, ``crm``,
``trading``, ``youtube``, ``secop``, ``chismoso``, ...) appears inside
``src/albra_seed/`` outside the explicitly-allowed paths, that is a
genetic contamination event.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from albra_seed.contamination import (
    ALLOWED_PATHS,
    FORBIDDEN_DOMAIN_KEYWORDS,
    ContaminationFinding,
    ContaminationReport,
    ContaminationScanner,
)

# Project root for the "scan the real seed" test.
# tests/contamination/test_contamination.py
#   parents[0] = tests/contamination
#   parents[1] = tests
#   parents[2] = project root (/home/z/my-project/agent-general)
SEED_PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_seed(tmp_path: Path) -> Path:
    """Create a temp ``<tmp>/project`` dir with an empty
    ``src/albra_seed/`` tree and return the project root path."""
    project_root = tmp_path / "project"
    (project_root / "src" / "albra_seed").mkdir(parents=True)
    return project_root


# ---------------------------------------------------------------------------
# 1. Scanner finds a forbidden keyword in a .py file
# ---------------------------------------------------------------------------


def test_scanner_finds_keyword_in_py_file(tmp_path: Path) -> None:
    """A .py file containing the word ``legal`` triggers a critical
    keyword finding."""
    project_root = _make_seed(tmp_path)
    (project_root / "src" / "albra_seed" / "foo.py").write_text(
        'msg = "this is legal advice"\n',
    )

    scanner = ContaminationScanner(project_root)
    report = scanner.scan()

    legal_findings = [f for f in report.findings if f.keyword == "legal"]
    assert len(legal_findings) == 1, f"expected 1 'legal' finding, got {report.findings}"
    f = legal_findings[0]
    assert f.path == "src/albra_seed/foo.py"
    assert f.severity == "critical"
    assert f.finding_type == "keyword"
    assert f.line == 1
    assert f.column >= 1
    assert "legal" in f.context.lower()


# ---------------------------------------------------------------------------
# 2. Scanner skips ALLOWED_PATHS
# ---------------------------------------------------------------------------


def test_scanner_skips_allowed_paths(tmp_path: Path) -> None:
    """A file at ``docs/SPECIALIZATION.md`` (an allowed path) that
    mentions ``legal`` produces NO findings."""
    project_root = tmp_path / "project"
    docs_dir = project_root / "docs"
    docs_dir.mkdir(parents=True)
    spec_md = docs_dir / "SPECIALIZATION.md"
    spec_md.write_text("# Specializations\n\n- legal\n- crm\n- trading\n")

    scanner = ContaminationScanner(project_root)
    findings = scanner.scan_file(spec_md)
    assert findings == [], (
        f"docs/SPECIALIZATION.md is in ALLOWED_PATHS and must be skipped; "
        f"got findings: {findings}"
    )


# ---------------------------------------------------------------------------
# 3. Scanner skips __pycache__
# ---------------------------------------------------------------------------


def test_scanner_skips_pycache(tmp_path: Path) -> None:
    """Files inside ``__pycache__/`` MUST NOT be scanned."""
    project_root = _make_seed(tmp_path)
    pyc_dir = project_root / "src" / "albra_seed" / "__pycache__"
    pyc_dir.mkdir()
    (pyc_dir / "foo.py").write_text('msg = "legal here"\n')

    scanner = ContaminationScanner(project_root)
    report = scanner.scan()
    assert report.critical_findings == 0, (
        f"__pycache__ must be skipped; got: {report.findings}"
    )
    assert report.is_clean is True


# ---------------------------------------------------------------------------
# 4. Scanner detects forbidden code pattern: import
# ---------------------------------------------------------------------------


def test_scanner_finds_code_pattern_import(tmp_path: Path) -> None:
    """``import legal`` is a forbidden code pattern in .py files."""
    project_root = _make_seed(tmp_path)
    target = project_root / "src" / "albra_seed" / "imp.py"
    target.write_text("import legal\n")

    scanner = ContaminationScanner(project_root)
    findings = scanner.scan_file(target)
    code_findings = [f for f in findings if f.finding_type == "code_pattern"]
    assert len(code_findings) >= 1, f"expected a code_pattern finding, got {findings}"
    f = code_findings[0]
    assert f.severity == "critical"
    assert "import legal" in f.keyword
    assert f.line == 1


# ---------------------------------------------------------------------------
# 5. Scanner detects forbidden code pattern: class name
# ---------------------------------------------------------------------------


def test_scanner_finds_code_pattern_class_name(tmp_path: Path) -> None:
    """``class LegalAgent`` is a forbidden code pattern (domain-specific
    class name in the universal seed)."""
    project_root = _make_seed(tmp_path)
    target = project_root / "src" / "albra_seed" / "agent.py"
    target.write_text("class LegalAgent:\n    pass\n")

    scanner = ContaminationScanner(project_root)
    findings = scanner.scan_file(target)
    code_findings = [f for f in findings if f.finding_type == "code_pattern"]
    assert len(code_findings) >= 1, f"expected a code_pattern finding, got {findings}"
    f = code_findings[0]
    assert f.severity == "critical"
    assert "LegalAgent" in f.keyword
    assert f.line == 1


# ---------------------------------------------------------------------------
# 6. Scanner detects hardcoded domain assignment
# ---------------------------------------------------------------------------


def test_scanner_finds_hardcoded_domain(tmp_path: Path) -> None:
    """``domain = "trading"`` is a forbidden hardcoded domain config."""
    project_root = _make_seed(tmp_path)
    target = project_root / "src" / "albra_seed" / "cfg.py"
    target.write_text('domain = "trading"\n')

    scanner = ContaminationScanner(project_root)
    findings = scanner.scan_file(target)
    code_findings = [f for f in findings if f.finding_type == "code_pattern"]
    assert len(code_findings) >= 1, f"expected a code_pattern finding, got {findings}"
    f = code_findings[0]
    assert f.severity == "critical"
    assert "trading" in f.keyword


# ---------------------------------------------------------------------------
# 7. Whole-word matching — "legally" must NOT trigger "legal"
# ---------------------------------------------------------------------------


def test_scanner_whole_word_match(tmp_path: Path) -> None:
    """``legally`` is NOT a ``legal`` finding (whole-word match required)."""
    project_root = _make_seed(tmp_path)
    target = project_root / "src" / "albra_seed" / "words.py"
    # "legally" alone, no occurrence of "legal" as a whole word.
    target.write_text("# this is legally fine\n")

    scanner = ContaminationScanner(project_root)
    findings = scanner.scan_file(target)
    legal_findings = [f for f in findings if f.keyword == "legal"]
    assert legal_findings == [], (
        f"'legally' must NOT trigger a 'legal' finding (whole-word); "
        f"got: {legal_findings}"
    )


# ---------------------------------------------------------------------------
# 8. Case-insensitive matching — "LEGAL" IS a finding for "legal"
# ---------------------------------------------------------------------------


def test_scanner_case_insensitive(tmp_path: Path) -> None:
    """``LEGAL`` matches the forbidden keyword ``legal`` (case-insensitive)."""
    project_root = _make_seed(tmp_path)
    target = project_root / "src" / "albra_seed" / "upper.py"
    target.write_text('x = "LEGAL"\n')

    scanner = ContaminationScanner(project_root)
    findings = scanner.scan_file(target)
    legal_findings = [f for f in findings if f.keyword == "legal"]
    assert len(legal_findings) == 1, (
        f"expected case-insensitive 'legal' finding, got {findings}"
    )


# ---------------------------------------------------------------------------
# 9. The actual ALBRA Seed must be CLEAN
# ---------------------------------------------------------------------------


def test_scanner_clean_seed() -> None:
    """Scan the real Seed at ``/home/z/my-project/agent-general/`` and
    verify zero critical findings. Any false positive here means the
    keyword list or ALLOWED_PATHS needs adjustment — NOT the seed
    code itself."""
    assert SEED_PROJECT_ROOT.exists(), (
        f"seed project root not found at {SEED_PROJECT_ROOT}"
    )
    scanner = ContaminationScanner(SEED_PROJECT_ROOT)
    report = scanner.scan()

    assert report.total_files_scanned > 0, "scanner should have scanned some files"
    assert report.critical_findings == 0, (
        f"seed is contaminated (critical findings): {report.findings}"
    )
    assert report.is_clean is True


# ---------------------------------------------------------------------------
# 10. ContaminationReport is a pydantic BaseModel with required fields
# ---------------------------------------------------------------------------


def test_scanner_report_is_pydantic_model(tmp_path: Path) -> None:
    """``ContaminationReport`` must be a pydantic v2 BaseModel with the
    required fields and correct types."""
    assert issubclass(ContaminationReport, BaseModel)
    assert issubclass(ContaminationFinding, BaseModel)

    project_root = _make_seed(tmp_path)
    (project_root / "src" / "albra_seed" / "x.py").write_text('y = "legal"\n')
    scanner = ContaminationScanner(project_root)
    report = scanner.scan()

    # Required fields exist and have the right types.
    assert isinstance(report.total_files_scanned, int)
    assert isinstance(report.total_findings, int)
    assert isinstance(report.critical_findings, int)
    assert isinstance(report.warning_findings, int)
    assert isinstance(report.findings, list)
    assert isinstance(report.is_clean, bool)
    assert hasattr(report, "scanned_at")

    # pydantic v2: model_dump() returns a dict of all fields.
    d = report.model_dump()
    for key in (
        "total_files_scanned",
        "total_findings",
        "critical_findings",
        "warning_findings",
        "findings",
        "is_clean",
        "scanned_at",
    ):
        assert key in d, f"missing field {key!r} in model_dump()"

    # The first finding must itself be a ContaminationFinding instance.
    if report.findings:
        assert isinstance(report.findings[0], ContaminationFinding)
        f = report.findings[0]
        assert isinstance(f.path, str)
        assert isinstance(f.line, int)
        assert isinstance(f.column, int)
        assert isinstance(f.keyword, str)
        assert isinstance(f.context, str)
        assert f.severity in {"critical", "warning"}
        assert f.finding_type in {"keyword", "code_pattern"}


# ---------------------------------------------------------------------------
# 11. Severity: keyword in .py -> critical; keyword in .md (outside
#     ALLOWED_PATHS) -> warning
# ---------------------------------------------------------------------------


def test_scanner_distinguishes_critical_vs_warning(tmp_path: Path) -> None:
    """A forbidden keyword in a ``.py`` file is ``critical``; the same
    keyword in a ``.md`` file OUTSIDE ALLOWED_PATHS is ``warning``."""
    project_root = _make_seed(tmp_path)
    seed_dir = project_root / "src" / "albra_seed"
    # .py file → critical.
    (seed_dir / "contam.py").write_text('msg = "legal"\n')
    # .md file directly under src/albra_seed/ — NOT in ALLOWED_PATHS —
    # so it is scanned but classified as a warning.
    (seed_dir / "notes.md").write_text("# Notes\n\nThis mentions legal stuff.\n")

    scanner = ContaminationScanner(project_root)
    report = scanner.scan()

    py_findings = [f for f in report.findings if f.path.endswith(".py")]
    md_findings = [f for f in report.findings if f.path.endswith(".md")]

    assert py_findings, "expected at least one .py finding"
    assert all(f.severity == "critical" for f in py_findings), (
        f"keyword in .py must be critical; got: {py_findings}"
    )
    assert md_findings, "expected at least one .md finding"
    assert all(f.severity == "warning" for f in md_findings), (
        f"keyword in .md (outside ALLOWED_PATHS) must be warning; got: {md_findings}"
    )


# ---------------------------------------------------------------------------
# Bonus: forbidden keyword catalog and ALLOWED_PATHS are well-formed
# ---------------------------------------------------------------------------


def test_forbidden_keyword_catalog_well_formed() -> None:
    """The FORBIDDEN_DOMAIN_KEYWORDS catalog must be a non-empty list
    of unique, lowercase-ish strings, and ALLOWED_PATHS must be a
    non-empty list of relative paths (no leading slash)."""
    assert isinstance(FORBIDDEN_DOMAIN_KEYWORDS, list)
    assert len(FORBIDDEN_DOMAIN_KEYWORDS) > 5
    # Unique (case-insensitive).
    lower = [k.lower() for k in FORBIDDEN_DOMAIN_KEYWORDS]
    assert len(lower) == len(set(lower)), "duplicate keywords in catalog"
    # ALLOWED_PATHS sanity.
    assert isinstance(ALLOWED_PATHS, list)
    assert len(ALLOWED_PATHS) > 0
    for p in ALLOWED_PATHS:
        assert isinstance(p, str)
        assert not p.startswith("/"), (
            f"ALLOWED_PATHS entries must be relative, got {p!r}"
        )
