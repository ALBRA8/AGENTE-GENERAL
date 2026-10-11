"""ContaminationScanner — walks ``src/albra_seed/`` and reports any
forbidden domain keyword or code pattern found in its source files.

The scanner is the runtime behind the ALBRA **Genetic Contamination
Test**. It is deliberately dependency-free (only stdlib + pydantic v2,
which is already required by ``albra_seed.core``). All filesystem
walking uses ``pathlib``.

Design notes:

  * **Severity model**:
      - keyword match in a ``.py`` file  -> ``critical`` (real code)
      - keyword match in any other scannable file (``.md``, ``.txt``,
        ``.yaml``, ``.json``, ``.toml``) outside ``ALLOWED_PATHS`` ->
        ``warning`` (likely documentation that needs review)
      - any forbidden code pattern (import / from-import / class /
        hardcoded ``domain = "..."``) -> ``critical`` (these only
        apply to ``.py`` files; they are syntactic contamination)

  * **Whole-word matching** for keywords: ``\b{keyword}\b`` with
    ``re.IGNORECASE``. So "legally" does NOT trigger the "legal"
    finding, and "LEGAL" DOES trigger it.

  * **ALLOWED_PATHS** are matched as either exact relative paths
    (e.g. ``src/albra_seed/__init__.py``) or as directory prefixes
    (e.g. ``src/albra_seed/contamination/`` matches everything under
    that subpackage). Paths are normalised to forward slashes before
    comparison so the scanner works on Windows too.

  * **Skip directories** during the walk: ``__pycache__``, ``.git``,
    ``node_modules``, ``.venv`` (and their nested copies).

  * **Failure modes**: unreadable files are silently skipped (the
    scanner never raises on I/O errors); the ``findings`` list simply
    omits them. This is intentional — the scanner must always produce
    a report, even on a half-broken checkout.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from albra_seed.contamination.domains import (
    ALLOWED_PATHS,
    FORBIDDEN_CODE_PATTERNS,
    FORBIDDEN_DOMAIN_KEYWORDS,
    SCANNABLE_EXTENSIONS,
)

# Directories that are NEVER scanned, regardless of location.
_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".git", "node_modules", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", "egg-info",
})


# ---------------------------------------------------------------------------
# Pydantic v2 models — public contract of the contamination report
# ---------------------------------------------------------------------------


class ContaminationFinding(BaseModel):
    """A single contamination finding inside one file."""

    path: str = Field(..., description="File path relative to project root (forward slashes).")
    line: int = Field(..., ge=1, description="1-based line number where the finding occurs.")
    column: int = Field(..., ge=1, description="1-based column where the match starts.")
    keyword: str = Field(..., description="The forbidden keyword or matched code-pattern text.")
    context: str = Field(..., description="Trimmed content of the line containing the finding.")
    severity: str = Field(..., description="'critical' or 'warning'.")
    finding_type: str = Field(..., description="'keyword' or 'code_pattern'.")


class ContaminationReport(BaseModel):
    """Aggregate contamination report produced by ``ContaminationScanner.scan()``."""

    total_files_scanned: int = Field(..., ge=0)
    total_findings: int = Field(..., ge=0)
    critical_findings: int = Field(..., ge=0)
    warning_findings: int = Field(..., ge=0)
    findings: list[ContaminationFinding] = Field(default_factory=list)
    is_clean: bool = Field(..., description="True iff critical_findings == 0.")
    scanned_at: datetime = Field(..., description="UTC timestamp the scan completed.")


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class ContaminationScanner:
    """Walks ``<project_root>/src/albra_seed/`` and reports contamination.

    The scanner is constructed with a ``project_root`` and (optionally)
    overrides for the forbidden keyword list, allowed-path list, and
    forbidden code-pattern regex list. All arguments are keyword-only
    after ``project_root`` so callers can override exactly one without
    having to repeat the defaults for the others.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        forbidden_keywords: Iterable[str] | None = None,
        allowed_paths: Iterable[str] | None = None,
        forbidden_patterns: Iterable[str] | None = None,
    ) -> None:
        self.project_root: Path = Path(project_root).resolve()
        self.forbidden_keywords: list[str] = list(
            forbidden_keywords if forbidden_keywords is not None else FORBIDDEN_DOMAIN_KEYWORDS
        )
        self.allowed_paths: list[str] = list(
            allowed_paths if allowed_paths is not None else ALLOWED_PATHS
        )
        # Pre-compile the keyword regexes once.
        self._keyword_regexes: list[tuple[str, re.Pattern[str]]] = [
            (kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE))
            for kw in self.forbidden_keywords
        ]
        # Pre-compile the code-pattern regexes (applied to .py files only).
        raw_patterns = list(
            forbidden_patterns if forbidden_patterns is not None else FORBIDDEN_CODE_PATTERNS
        )
        self._code_patterns: list[re.Pattern[str]] = [re.compile(p) for p in raw_patterns]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> ContaminationReport:
        """Walk ``project_root/src/albra_seed/`` and scan every scannable
        file. Returns a populated :class:`ContaminationReport`."""
        seed_root = self.project_root / "src" / "albra_seed"
        findings: list[ContaminationFinding] = []
        files_scanned = 0

        if seed_root.exists():
            for path in sorted(seed_root.rglob("*")):
                if not path.is_file():
                    continue
                # Skip any file whose path contains a skipped directory.
                if any(part in _SKIP_DIRS for part in path.parts):
                    continue
                if path.suffix not in SCANNABLE_EXTENSIONS:
                    continue
                files_scanned += 1
                findings.extend(self.scan_file(path))

        critical = sum(1 for f in findings if f.severity == "critical")
        warning = sum(1 for f in findings if f.severity == "warning")
        return ContaminationReport(
            total_files_scanned=files_scanned,
            total_findings=len(findings),
            critical_findings=critical,
            warning_findings=warning,
            findings=findings,
            is_clean=(critical == 0),
            scanned_at=datetime.now(timezone.utc),
        )

    def scan_file(self, path: Path) -> list[ContaminationFinding]:
        """Scan a single file. Returns ``[]`` when:

          * the file is in :data:`ALLOWED_PATHS`,
          * the file extension is not in
            :data:`~albra_seed.contamination.domains.SCANNABLE_EXTENSIONS`,
          * the file cannot be read as text.

        Otherwise returns one :class:`ContaminationFinding` per
        forbidden keyword occurrence (whole-word, case-insensitive) and
        per forbidden code-pattern occurrence (Python files only).
        """
        rel_str = self._relative_str(path)
        if self._is_allowed(rel_str):
            return []

        path_obj = Path(path)
        if path_obj.suffix not in SCANNABLE_EXTENSIONS:
            return []

        try:
            content = path_obj.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # Unreadable file — skip silently. The scanner must always
            # produce a report, even on a half-broken checkout.
            return []

        is_python = path_obj.suffix == ".py"
        # Keywords: warning for non-Python, critical for Python.
        keyword_severity = "critical" if is_python else "warning"
        findings: list[ContaminationFinding] = []

        for lineno, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            # Keyword scan (whole-word, case-insensitive).
            for keyword, regex in self._keyword_regexes:
                for m in regex.finditer(line):
                    findings.append(ContaminationFinding(
                        path=rel_str,
                        line=lineno,
                        column=m.start() + 1,
                        keyword=keyword,
                        context=stripped,
                        severity=keyword_severity,
                        finding_type="keyword",
                    ))
            # Code-pattern scan — Python only.
            if is_python:
                for regex in self._code_patterns:
                    for m in regex.finditer(line):
                        findings.append(ContaminationFinding(
                            path=rel_str,
                            line=lineno,
                            column=m.start() + 1,
                            keyword=m.group(0),
                            context=stripped,
                            severity="critical",
                            finding_type="code_pattern",
                        ))
        return findings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _relative_str(self, path: Path) -> str:
        """Return ``path`` as a forward-slashed string relative to
        ``project_root``. If ``path`` is not under ``project_root``,
        return its absolute path normalised to forward slashes."""
        try:
            rel = Path(path).resolve().relative_to(self.project_root)
            return str(rel).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def _is_allowed(self, rel_path_str: str) -> bool:
        """Return True iff ``rel_path_str`` matches an entry in
        :attr:`allowed_paths` either exactly (file) or as a directory
        prefix (entry ends with ``/`` or matches a parent dir)."""
        rel_path_str = rel_path_str.replace("\\", "/")
        for allowed in self.allowed_paths:
            allowed_norm = allowed.replace("\\", "/").rstrip("/")
            if not allowed_norm:
                continue
            if rel_path_str == allowed_norm:
                return True
            if rel_path_str.startswith(allowed_norm + "/"):
                return True
        return False
