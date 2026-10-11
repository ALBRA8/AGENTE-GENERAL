"""Unit tests for the doctor subpackage (F2-G)."""

from __future__ import annotations

import asyncio

import pytest

from albra_seed.doctor import (
    CheckRegistry,
    DiagnosticMatrix,
    DiagnosticReport,
    Doctor,
    ImplementationStatus,
    default_registry,
)
from albra_seed.doctor.report import ComponentDiagnostic
from albra_seed.core.types import HealthState


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# Doctor returns a DiagnosticReport
# =====================================================================


def test_doctor_returns_diagnostic_report():
    """Doctor.diagnose() returns a DiagnosticReport with components."""
    doctor = Doctor()
    report = _run(doctor.diagnose())
    assert isinstance(report, DiagnosticReport)
    # The default registry has 10 built-in checks.
    assert len(report.components) == 10
    # Every component has a status + implementation_status set.
    for c in report.components:
        assert c.name
        assert isinstance(c.status, HealthState)
        assert isinstance(c.implementation_status, ImplementationStatus)
        assert c.duration_ms is not None
        assert c.duration_ms >= 0
    # Summary rolls up counts.
    s = report.summary()
    assert s["component_count"] == 10
    assert "status_counts" in s
    assert "implementation_counts" in s


# =====================================================================
# Doctor distinguishes MOCK from IMPLEMENTED
# =====================================================================


def test_doctor_distinguishes_mock_from_implemented():
    """A check that returns IMPLEMENTED is distinct from one that
    returns MOCK. The doctor MUST NOT upgrade a MOCK result to HEALTHY."""
    reg = CheckRegistry()
    async def implemented_check():
        from albra_seed.doctor.checks import CheckResult
        return CheckResult(
            name="impl",
            status=HealthState.HEALTHY,
            implementation_status=ImplementationStatus.IMPLEMENTED,
            details="real probe passed",
            evidence=["probe ran"],
        )
    async def mock_check():
        from albra_seed.doctor.checks import CheckResult
        return CheckResult(
            name="mock",
            status=HealthState.HEALTHY,
            implementation_status=ImplementationStatus.MOCK,
            details="using mock implementation",
        )
    reg.register("impl", implemented_check)
    reg.register("mock", mock_check)
    doctor = Doctor(registry=reg)
    report = _run(doctor.diagnose())
    impl = report.get("impl")
    mock = report.get("mock")
    assert impl.implementation_status == ImplementationStatus.IMPLEMENTED
    assert mock.implementation_status == ImplementationStatus.MOCK
    assert impl.implementation_status != mock.implementation_status


def test_doctor_check_failure_returns_error_status():
    """If a check raises, the doctor records ERROR + NOT_VERIFIED,
    it does NOT crash."""
    reg = CheckRegistry()
    async def boom_check():
        raise RuntimeError("boom")
    reg.register("boom", boom_check)
    doctor = Doctor(registry=reg)
    report = _run(doctor.diagnose())
    diag = report.get("boom")
    assert diag.status == HealthState.ERROR
    assert diag.implementation_status == ImplementationStatus.NOT_VERIFIED
    assert "boom" in diag.details


# =====================================================================
# Diagnostic matrix includes all components
# =====================================================================


def test_doctor_matrix_includes_all_components():
    """The default DiagnosticMatrix has rows for every SEED component
    listed in the spec (runtime, memory, providers, tools, security,
    audit, observability, recovery, doctor, ...)."""
    matrix = DiagnosticMatrix.default()
    components = matrix.components()
    # Spot-check that all the major subsystems are represented.
    required_prefixes = [
        "runtime", "memory", "providers", "tools",
        "security", "firewall", "kill_switch", "policy", "autonomy",
        "approvals", "evidence", "provenance", "execution",
        "workflows", "jobs", "scheduler", "resilience",
        "observability", "audit", "doctor", "recovery",
    ]
    for prefix in required_prefixes:
        assert any(c == prefix or c.startswith(prefix + ".") for c in components), (
            f"matrix missing component family '{prefix}'"
        )
    # Rows default to NOT_VERIFIED until the doctor overlays real checks.
    for row in matrix.rows:
        assert row.status == HealthState.NOT_VERIFIED


def test_doctor_overlay_report_on_matrix():
    """Doctor.overlay_report_on_matrix copies the report's per-component
    status/evidence onto a copy of the matrix."""
    reg = CheckRegistry()
    async def runtime_check():
        from albra_seed.doctor.checks import CheckResult
        return CheckResult(
            name="runtime",
            status=HealthState.HEALTHY,
            implementation_status=ImplementationStatus.IMPLEMENTED,
            details="runtime ok",
            evidence=["probe ran"],
        )
    reg.register("runtime", runtime_check)
    doctor = Doctor(registry=reg)
    report = _run(doctor.diagnose())
    overlay = doctor.overlay_report_on_matrix(report)
    # The runtime row should now be HEALTHY/IMPLEMENTED.
    runtime_row = next(r for r in overlay.rows if r.component == "runtime")
    assert runtime_row.status == HealthState.HEALTHY
    assert runtime_row.implementation == ImplementationStatus.IMPLEMENTED
    # Other rows are untouched.
    other_rows = [r for r in overlay.rows if r.component != "runtime"]
    assert all(r.status == HealthState.NOT_VERIFIED for r in other_rows)


def test_doctor_matrix_axes_present():
    """The matrix exposes all 8 axes: component, status, implementation,
    test, dependency, risk, evidence, notes."""
    matrix = DiagnosticMatrix.default()
    d = matrix.to_dict()
    axes = set(d["axes"])
    expected = {
        "component", "status", "implementation", "test",
        "dependency", "risk", "evidence", "notes",
    }
    assert expected.issubset(axes)
    # Each row has all axes.
    for row in d["rows"]:
        assert expected.issubset(set(row.keys()))


def test_default_registry_runs_all_checks():
    """default_registry() returns 10 checks; running them yields 10 results."""
    reg = default_registry()
    assert len(reg.names()) == 10
    results = _run(reg.run_all())
    assert len(results) == 10
    # No check should be in NOT_CONFIGURED state for core packages.
    core_names = {"runtime", "memory", "providers", "tools", "skills",
                  "security", "audit", "observability"}
    for r in results:
        if r.name in core_names:
            assert r.status in {HealthState.HEALTHY, HealthState.WARNING, HealthState.ERROR}, (
                f"core check {r.name} unexpectedly returned status={r.status}"
            )
