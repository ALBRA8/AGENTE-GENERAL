"""Doctor subpackage — runtime diagnostics.

Public API:
    Doctor              — runs checks, produces a DiagnosticReport.
    CheckRegistry       — registry of named async checks.
    default_registry()  — pre-populated with built-in checks.
    DiagnosticReport    — aggregate per-run report.
    DiagnosticMatrix    — static component × status × impl × test ×
                            dependency × risk × evidence × notes matrix
                            (spec section 69).

CRITICAL RULE: a component is reported HEALTHY+IMPLEMENTED only if its
check actually ran and produced positive evidence. A structurally-
present-but-unchecked component is reported NOT_VERIFIED.
"""

from albra_seed.doctor.builtin_checks import (
    audit_check,
    comm_check,
    default_registry,
    mcp_check,
    memory_check,
    observability_check,
    providers_check,
    runtime_check,
    security_check,
    skills_check,
    tools_check,
)
from albra_seed.doctor.checks import (
    CheckFn,
    CheckRegistry,
    CheckResult,
)
from albra_seed.doctor.doctor import Doctor
from albra_seed.doctor.matrix import DiagnosticMatrix, MatrixRow
from albra_seed.doctor.report import (
    ComponentDiagnostic,
    DiagnosticReport,
    ImplementationStatus,
)

__all__ = [
    "Doctor",
    "CheckRegistry",
    "CheckResult",
    "CheckFn",
    "default_registry",
    "DiagnosticReport",
    "ComponentDiagnostic",
    "ImplementationStatus",
    "DiagnosticMatrix",
    "MatrixRow",
    # Built-in checks
    "runtime_check",
    "memory_check",
    "providers_check",
    "tools_check",
    "skills_check",
    "security_check",
    "audit_check",
    "observability_check",
    "mcp_check",
    "comm_check",
]
