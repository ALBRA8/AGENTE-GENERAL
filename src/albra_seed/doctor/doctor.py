"""Doctor — runtime diagnostics.

The Doctor runs every registered check and produces a DiagnosticReport.
CRITICAL RULE: a component is reported HEALTHY+IMPLEMENTED only if the
check actually ran and produced positive evidence. A structurally-
present-but-unchecked component is reported NOT_VERIFIED.

The Doctor also overlays check results onto the static DiagnosticMatrix
so the matrix reflects current observations rather than the spec.
"""

from __future__ import annotations

import asyncio
from typing import Any

from albra_seed.doctor.builtin_checks import default_registry
from albra_seed.doctor.checks import CheckRegistry, CheckResult
from albra_seed.doctor.matrix import DiagnosticMatrix
from albra_seed.doctor.report import (
    ComponentDiagnostic,
    DiagnosticReport,
    ImplementationStatus,
)


class Doctor:
    """Runs diagnostic checks and produces a DiagnosticReport."""

    def __init__(
        self,
        registry: CheckRegistry | None = None,
        matrix: DiagnosticMatrix | None = None,
    ) -> None:
        self._registry = registry or default_registry()
        self._matrix = matrix or DiagnosticMatrix.default()

    @property
    def registry(self) -> CheckRegistry:
        return self._registry

    @property
    def matrix(self) -> DiagnosticMatrix:
        return self._matrix

    def register_check(self, name: str, fn: Any) -> None:
        self._registry.register(name, fn)

    # ------------------------------------------------------------------
    # Diagnosis
    # ------------------------------------------------------------------
    async def diagnose(self) -> DiagnosticReport:
        """Run every registered check and build a DiagnosticReport."""
        results = await self._registry.run_all()
        report = DiagnosticReport()
        for r in results:
            report.add(r.to_diagnostic())
        return report

    def diagnose_sync(self) -> DiagnosticReport:
        """Synchronous convenience wrapper around `diagnose()`."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.diagnose())
        # If there's already a loop, can't asyncio.run — create a fresh task.
        # Doctor diagnostics are short-lived; this should be rare.
        raise RuntimeError(
            "Doctor.diagnose_sync() cannot run inside an existing event loop; "
            "use `await doctor.diagnose()` instead."
        )

    # ------------------------------------------------------------------
    # Matrix overlay
    # ------------------------------------------------------------------
    def overlay_report_on_matrix(self, report: DiagnosticReport) -> DiagnosticMatrix:
        """Return a copy of the matrix with status / evidence fields
        updated from the report's per-component diagnostics."""
        new_matrix = DiagnosticMatrix(rows=[r.model_copy() for r in self._matrix.rows])
        by_name = {c.name: c for c in report.components}
        for row in new_matrix.rows:
            diag = by_name.get(row.component)
            if diag is None:
                # Try matching by prefix (e.g. matrix "memory.inmemory"
                # vs check name "memory").
                prefix = row.component.split(".")[0]
                diag = by_name.get(prefix)
            if diag is not None:
                row.status = diag.status
                row.implementation = diag.implementation_status
                row.evidence = list(diag.evidence)
                row.notes = diag.details
        return new_matrix

    def list_components(self) -> list[str]:
        return self._matrix.components()


__all__ = ["Doctor"]
