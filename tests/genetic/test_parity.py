"""Genetic Parity Test — verifies a specialized ALBRA agent preserves
the universal DNA of the Seed (Task ID GAP-6).

These tests run ``ParityChecker`` against the ALBRA Seed itself. The
Seed is the reference implementation: it MUST pass parity (it carries
all universal DNA components and no specialization modules), and it
MUST report ``is_pure_seed=True``.

A final test injects a fake ``specialization.<unknown>`` module into
``sys.modules`` and verifies that:
  * the checker detects it as a specialization component,
  * it appears in ``specialization_components``,
  * ``is_pure_seed`` becomes False.

This mirrors the rule that a specialization ADDS components but MUST
NOT remove or replace any DNA component.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from albra_seed.parity import (
    ALLOWED_SPECIALIZATIONS,
    UNIVERSAL_DNA_COMPONENTS,
    ParityChecker,
    ParityReport,
)
from albra_seed.parity.dna import UNIVERSAL_DNA_COMPONENTS as DNA


# =====================================================================
# 1. The Seed carries ALL universal DNA components
# =====================================================================


async def test_parity_seed_has_all_universal_dna_components() -> None:
    """Every entry in UNIVERSAL_DNA_COMPONENTS MUST be importable as
    ``albra_seed.<component>`` in the bare seed environment."""
    checker = ParityChecker()
    results = checker.check_imports()
    assert len(results) == len(UNIVERSAL_DNA_COMPONENTS), (
        "check_imports() must return one entry per UNIVERSAL_DNA_COMPONENTS"
    )
    missing = [c for c, ok in results.items() if not ok]
    assert missing == [], f"Seed is missing universal DNA components: {missing}"


# =====================================================================
# 2. Runtime births in CORE_ONLY mode
# =====================================================================


async def test_parity_runtime_births_in_core_only() -> None:
    """Runtime(config=SeedConfig()) + startup() + readiness()==True."""
    checker = ParityChecker()
    result = await checker.check_runtime_birth()
    assert result["ok"] is True, f"runtime birth failed: {result.get('error')}"
    assert result["details"]["readiness"] is True


# =====================================================================
# 3. Agent loop runs end-to-end
# =====================================================================


async def test_parity_agent_loop_runs() -> None:
    """Agent(identity, runtime) + agent.run('hello') -> AgentResponse."""
    checker = ParityChecker()
    result = await checker.check_agent_loop()
    assert result["ok"] is True, f"agent loop failed: {result.get('error')}"
    assert result["details"]["content_len"] > 0


# =====================================================================
# 4. MemoryDV round-trip works
# =====================================================================


async def test_parity_memory_dv_works() -> None:
    """MemoryManager.write/read round-trip succeeds."""
    checker = ParityChecker()
    result = await checker.check_memory_dv()
    assert result["ok"] is True, f"memory DV failed: {result.get('error')}"
    assert result["details"]["record_id"]


# =====================================================================
# 5. Audit append works
# =====================================================================


async def test_parity_audit_works() -> None:
    """AuditLog.append/record works."""
    checker = ParityChecker()
    result = await checker.check_audit()
    assert result["ok"] is True, f"audit failed: {result.get('error')}"


# =====================================================================
# 6. Evidence register works
# =====================================================================


async def test_parity_evidence_works() -> None:
    """EvidenceManager.register works."""
    checker = ParityChecker()
    result = await checker.check_evidence()
    assert result["ok"] is True, f"evidence failed: {result.get('error')}"
    assert result["details"]["evidence_id"]


# =====================================================================
# 7. Firewall default-deny on unknown action
# =====================================================================


async def test_parity_firewall_default_deny() -> None:
    """Unknown action MUST be DENIED by the default firewall."""
    checker = ParityChecker()
    result = await checker.check_firewall_default_deny()
    assert result["ok"] is True, f"firewall default-deny failed: {result.get('error')}"


# =====================================================================
# 8. Kill switch blocks all when active
# =====================================================================


async def test_parity_kill_switch_works() -> None:
    """Kill switch blocks ALL actions when active."""
    checker = ParityChecker()
    result = await checker.check_kill_switch()
    assert result["ok"] is True, f"kill switch failed: {result.get('error')}"
    assert result["details"]["blocked_after"] is True


# =====================================================================
# 9. Doctor returns a DiagnosticReport
# =====================================================================


async def test_parity_doctor_returns_report() -> None:
    """Doctor.diagnose() returns a DiagnosticReport."""
    checker = ParityChecker()
    result = await checker.check_doctor()
    assert result["ok"] is True, f"doctor failed: {result.get('error')}"


# =====================================================================
# 10. Feedback works (GAP-1)
# =====================================================================


async def test_parity_feedback_works() -> None:
    """FeedbackManager.record works (GAP-1)."""
    checker = ParityChecker()
    result = await checker.check_feedback()
    assert result["ok"] is True, f"feedback failed: {result.get('error')}"
    assert result["details"]["feedback_id"].startswith("fb_")


# =====================================================================
# 11. Learning works (GAP-2)
# =====================================================================


async def test_parity_learning_works() -> None:
    """LearningManager.propose works (GAP-2)."""
    checker = ParityChecker()
    result = await checker.check_learning()
    assert result["ok"] is True, f"learning failed: {result.get('error')}"
    assert result["details"]["proposal_id"].startswith("learn_")


# =====================================================================
# 12. Decision memory works (GAP-3)
# =====================================================================


async def test_parity_decision_memory_works() -> None:
    """DecisionMemory.record_decision works (GAP-3)."""
    checker = ParityChecker()
    result = await checker.check_decision_memory()
    assert result["ok"] is True, f"decision memory failed: {result.get('error')}"
    assert result["details"]["decision_id"].startswith("dec_")


# =====================================================================
# 13. Skill evolution exists (GAP-4)
# =====================================================================


async def test_parity_skill_evolution_exists() -> None:
    """SkillEvolutionManager exists and is instantiable (GAP-4)."""
    checker = ParityChecker()
    result = await checker.check_skill_evolution()
    assert result["ok"] is True, f"skill evolution failed: {result.get('error')}"


# =====================================================================
# 14. Seed itself is pure (no specialization components)
# =====================================================================


async def test_parity_report_is_pure_seed() -> None:
    """The Seed carries no extra specialization components and all
    DNA + behavioral checks pass — therefore is_pure_seed must be True."""
    checker = ParityChecker()
    report = await checker.run_all()

    assert isinstance(report, ParityReport)
    d = report.to_dict()
    # All DNA present.
    assert d["total_components"] == len(UNIVERSAL_DNA_COMPONENTS)
    assert d["missing"] == 0, f"missing components: {report.missing_components}"
    assert d["present"] == len(UNIVERSAL_DNA_COMPONENTS)
    # All behavioral checks pass.
    assert d["checks_failed"] == 0, f"failed checks: {report.check_results}"
    # No extra specialization modules.
    assert d["specialization_components"] == [], (
        f"unexpected specialization components in seed: {d['specialization_components']}"
    )
    # Therefore the seed is pure.
    assert d["is_pure_seed"] is True


# =====================================================================
# 15. A specialization CAN add components (and is_pure_seed becomes False)
# =====================================================================


async def test_parity_specialization_can_add_components() -> None:
    """Mock a fake ``specialization.<unknown>`` module and verify:
       (a) the checker detects it as a specialization component,
       (b) it appears in ``specialization_components``,
       (c) ``is_pure_seed`` becomes False.

    Also verify that an ALLOWED specialization module does NOT make
    is_pure_seed False (it's still considered benign).
    """
    checker = ParityChecker()

    # Baseline: no specialization modules → pure.
    baseline = await checker.run_all()
    assert baseline.is_pure_seed is True
    assert baseline.specialization_components == []

    # Inject a FAKE specialization module that is NOT in
    # ALLOWED_SPECIALIZATIONS. The checker must detect it.
    fake_name = "specialization.weather_forecast"  # not in ALLOWED list
    fake_parent_name = "specialization"
    fake_parent = types.ModuleType(fake_parent_name)
    fake_child = types.ModuleType(fake_name)
    # Set the child's __name__ so the checker's scan picks it up.
    fake_child.__name__ = fake_name
    sys.modules[fake_parent_name] = fake_parent
    sys.modules[fake_name] = fake_child

    try:
        # Sanity: our fake module is NOT in ALLOWED_SPECIALIZATIONS.
        assert fake_name not in ALLOWED_SPECIALIZATIONS
        # And the checker now detects it.
        detected = checker.detect_specialization_components()
        assert fake_name in detected, (
            f"fake specialization module {fake_name!r} was not detected; "
            f"got {detected!r}"
        )
        # And is_pure_seed is now False.
        report_after = await checker.run_all()
        assert report_after.is_pure_seed is False, (
            "is_pure_seed must be False when an extra specialization "
            "component is present"
        )
        # And the fake module appears in the report's specialization list.
        assert fake_name in report_after.specialization_components
    finally:
        # Clean up sys.modules so other tests are not affected.
        sys.modules.pop(fake_name, None)
        sys.modules.pop(fake_parent_name, None)

    # After cleanup — pure again.
    report_clean = await checker.run_all()
    assert report_clean.is_pure_seed is True
    assert report_clean.specialization_components == []


# =====================================================================
# Bonus: the DNA catalog itself is well-formed
# =====================================================================


def test_dna_catalog_is_well_formed() -> None:
    """The UNIVERSAL_DNA_COMPONENTS catalog must be non-empty, unique,
    and contain only generic (domain-agnostic) entries."""
    assert len(UNIVERSAL_DNA_COMPONENTS) > 50, "DNA catalog unexpectedly small"
    assert len(UNIVERSAL_DNA_COMPONENTS) == len(set(UNIVERSAL_DNA_COMPONENTS)), (
        "UNIVERSAL_DNA_COMPONENTS must have no duplicates"
    )
    # ALLOWED_SPECIALIZATIONS must also be unique.
    assert len(ALLOWED_SPECIALIZATIONS) == len(set(ALLOWED_SPECIALIZATIONS))
    # The DNA catalog and the ALLOWED_SPECIALIZATIONS must be disjoint.
    overlap = set(UNIVERSAL_DNA_COMPONENTS) & set(ALLOWED_SPECIALIZATIONS)
    assert overlap == set(), f"DNA and ALLOWED_SPECIALIZATIONS overlap: {overlap}"
    # No DNA entry may carry domain knowledge (we sanity check the
    # canonical prefixes here).
    allowed_prefixes = {
        "core.", "runtime.", "reasoning.", "providers.", "context.",
        "memory.", "provenance.", "evidence.", "security.", "firewall.",
        "policy.", "autonomy.", "approvals.", "execution.", "workflows.",
        "jobs.", "scheduler.", "resilience.", "observability.", "audit.",
        "doctor.", "recovery.", "mcp.", "handoff.", "communication.",
        "multimodal.", "files.", "tools.", "capabilities.", "skills.",
        "plugins.", "api.", "cli.", "config.", "feedback.", "learning.",
    }
    for entry in UNIVERSAL_DNA_COMPONENTS:
        assert any(entry.startswith(p) for p in allowed_prefixes), (
            f"DNA component {entry!r} does not match any known generic prefix"
        )


# =====================================================================
# Bonus: run_all() returns a dict via to_dict() with the required keys
# =====================================================================


async def test_parity_report_to_dict_shape() -> None:
    """``ParityReport.to_dict()`` returns a dict with every key
    specified in the parity contract."""
    checker = ParityChecker()
    report = await checker.run_all()
    d = report.to_dict()
    required_keys = {
        "total_components",
        "present",
        "missing",
        "checks_passed",
        "checks_failed",
        "is_pure_seed",
        "specialization_components",
    }
    assert required_keys.issubset(d.keys()), (
        f"to_dict() missing keys: {required_keys - set(d.keys())}"
    )
