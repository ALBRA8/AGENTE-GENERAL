"""ParityChecker + ParityReport — runs the Genetic Parity Test.

``ParityChecker`` is a self-contained probe that exercises an installed
ALBRA agent (the Seed itself, or a specialization) and produces a
``ParityReport``. The report answers two questions:

  1. **Is the universal DNA intact?** — every component in
     ``UNIVERSAL_DNA_COMPONENTS`` MUST be importable as
     ``albra_seed.<component>`` AND every behavioral check (runtime
     birth, agent loop, MemoryDV, audit, evidence, firewall default-
     deny, kill switch, doctor, feedback, learning, decision memory,
     skill evolution) MUST pass.

  2. **Is the agent a pure Seed?** — there must be NO extra
     specialization modules beyond ``ALLOWED_SPECIALIZATIONS``.
     Any module in ``sys.modules`` whose dotted name starts with
     ``specialization.`` AND is not in ``ALLOWED_SPECIALIZATIONS`` is
     reported in ``specialization_components`` and makes
     ``is_pure_seed`` False.

The checker is async (the underlying Seed APIs are async). It never
imports specialization modules itself; it only observes what is already
loaded in ``sys.modules``. It always uses CORE_ONLY mode (default
``SeedConfig()`` — no API keys, no external services).

Usage:
    from albra_seed.parity import ParityChecker
    report = await ParityChecker().run_all()
    print(report.to_dict())
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from dataclasses import dataclass, field
from typing import Any

from albra_seed.parity.dna import (
    ALLOWED_SPECIALIZATIONS,
    UNIVERSAL_DNA_COMPONENTS,
)


# ---------------------------------------------------------------------------
# ParityReport
# ---------------------------------------------------------------------------


@dataclass
class ParityReport:
    """Summary of a parity check run against an ALBRA agent."""

    total_components: int = 0
    present: int = 0
    missing: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    is_pure_seed: bool = False
    specialization_components: list[str] = field(default_factory=list)
    # Diagnostic detail (NOT part of the public summary surface, but
    # useful for the CLI script and for debugging failed checks).
    import_results: dict[str, bool] = field(default_factory=dict)
    check_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing_components: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_components": self.total_components,
            "present": self.present,
            "missing": self.missing,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "is_pure_seed": self.is_pure_seed,
            "specialization_components": list(self.specialization_components),
            "missing_components": list(self.missing_components),
        }


# ---------------------------------------------------------------------------
# ParityChecker
# ---------------------------------------------------------------------------


class ParityChecker:
    """Runs the Genetic Parity Test against an installed ALBRA agent.

    Construct with no args to test the default seed (``albra_seed``).
    Override ``root_package`` only if you are testing a fork that ships
    under a different top-level package name.
    """

    def __init__(self, root_package: str = "albra_seed") -> None:
        self.root_package = root_package

    # ------------------------------------------------------------------
    # 1. UNIVERSAL DNA IMPORT CHECK
    # ------------------------------------------------------------------

    def check_imports(self) -> dict[str, bool]:
        """Try to import each UNIVERSAL_DNA_COMPONENTS module under
        ``albra_seed.<component>``. Returns a dict of {component: bool}."""
        results: dict[str, bool] = {}
        for component in UNIVERSAL_DNA_COMPONENTS:
            full = f"{self.root_package}.{component}"
            try:
                importlib.import_module(full)
                results[component] = True
            except Exception:
                results[component] = False
        return results

    # ------------------------------------------------------------------
    # 2. KEY PUBLIC CLASSES CHECK
    # ------------------------------------------------------------------

    def check_classes(self) -> dict[str, bool]:
        """Verify that key public classes are importable from their
        canonical public modules. Returns a dict of {class_name: bool}."""
        targets: list[tuple[str, str, str]] = [
            # (module_path, class_name, expected_attr)
            ("albra_seed.runtime", "Runtime", "Runtime"),
            ("albra_seed.runtime", "Agent", "Agent"),
            ("albra_seed.memory", "MemoryManager", "MemoryManager"),
            ("albra_seed.audit", "AuditLog", "AuditLog"),
            ("albra_seed.evidence", "EvidenceManager", "EvidenceManager"),
            ("albra_seed.firewall", "ActionFirewall", "ActionFirewall"),
            ("albra_seed.firewall", "KillSwitch", "KillSwitch"),
            ("albra_seed.doctor", "Doctor", "Doctor"),
            ("albra_seed.doctor", "DiagnosticReport", "DiagnosticReport"),
            ("albra_seed.feedback", "FeedbackManager", "FeedbackManager"),
            ("albra_seed.learning", "LearningManager", "LearningManager"),
            ("albra_seed.memory", "DecisionMemory", "DecisionMemory"),
            ("albra_seed.skills", "SkillEvolutionManager", "SkillEvolutionManager"),
            ("albra_seed.core", "SeedConfig", "SeedConfig"),
            ("albra_seed.core", "AgentIdentity", "AgentIdentity"),
        ]
        results: dict[str, bool] = {}
        for module_path, class_name, attr in targets:
            try:
                mod = importlib.import_module(module_path)
                results[class_name] = hasattr(mod, attr)
            except Exception:
                results[class_name] = False
        return results

    # ------------------------------------------------------------------
    # 3. RUNTIME BIRTH CHECK
    # ------------------------------------------------------------------

    async def check_runtime_birth(self) -> dict[str, Any]:
        """Verify Runtime(config=SeedConfig()) + startup() + readiness()==True."""
        try:
            from albra_seed.core.config import SeedConfig
            from albra_seed.runtime import Runtime

            runtime = Runtime(config=SeedConfig())
            await runtime.startup()
            ready = runtime.readiness()
            await runtime.shutdown()
            return {
                "ok": bool(ready),
                "error": None if ready else "readiness() returned False after startup()",
                "details": {"readiness": ready},
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 4. AGENT LOOP CHECK
    # ------------------------------------------------------------------

    async def check_agent_loop(self) -> dict[str, Any]:
        """Verify Agent(identity, runtime) + agent.run('hello') returns AgentResponse."""
        try:
            from albra_seed.core.config import SeedConfig
            from albra_seed.core.identity import AgentIdentity
            from albra_seed.core.types import AgentResponse
            from albra_seed.runtime import Agent, Runtime

            runtime = Runtime(config=SeedConfig())
            await runtime.startup()
            identity = AgentIdentity(name="parity-check")
            agent = Agent(identity, runtime)
            response = await agent.run("hello")
            await runtime.shutdown()
            ok = isinstance(response, AgentResponse) and bool(response.content)
            return {
                "ok": ok,
                "error": None if ok else "agent.run() did not return a populated AgentResponse",
                "details": {
                    "response_type": type(response).__name__,
                    "content_len": len(response.content) if response.content else 0,
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 5. MEMORY DV CHECK
    # ------------------------------------------------------------------

    async def check_memory_dv(self) -> dict[str, Any]:
        """Verify MemoryManager.remember() + recall() round-trip works."""
        try:
            from albra_seed.memory import InMemoryMemoryStore, MemoryManager

            mgr = MemoryManager(InMemoryMemoryStore())
            record = await mgr.remember(
                agent_id="parity-agent",
                content="parity-probe",
            )
            records = await mgr.recall(agent_id="parity-agent")
            ok = any(r.content == "parity-probe" for r in records) and bool(record.record_id)
            return {
                "ok": ok,
                "error": None if ok else "MemoryManager round-trip did not return the written record",
                "details": {"record_id": record.record_id, "recalled": len(records)},
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 6. AUDIT CHECK
    # ------------------------------------------------------------------

    async def check_audit(self) -> dict[str, Any]:
        """Verify AuditLog.record_async() works."""
        try:
            from albra_seed.audit import AuditLog

            log = AuditLog()
            entry = await log.record_async(
                action="parity.check",
                actor="parity",
                result="success",
            )
            ok = entry is not None and entry.action == "parity.check"
            return {
                "ok": ok,
                "error": None if ok else "AuditLog.record_async() did not return a valid entry",
                "details": {"entry_id": getattr(entry, "entry_id", None)},
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 7. EVIDENCE CHECK
    # ------------------------------------------------------------------

    async def check_evidence(self) -> dict[str, Any]:
        """Verify EvidenceManager.register() works."""
        try:
            from albra_seed.evidence import EvidenceManager

            mgr = EvidenceManager()
            evidence = await mgr.register(
                source="parity",
                source_type="system",
                content="parity-probe",
                confidence=0.5,
            )
            ok = bool(evidence and evidence.evidence_id)
            return {
                "ok": ok,
                "error": None if ok else "EvidenceManager.register() did not return evidence",
                "details": {"evidence_id": getattr(evidence, "evidence_id", None)},
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 8. FIREWALL DEFAULT-DENY CHECK
    # ------------------------------------------------------------------

    async def check_firewall_default_deny(self) -> dict[str, Any]:
        """Verify an unknown action is DENIED by the default firewall."""
        try:
            from albra_seed.core.types import PolicyDecision
            from albra_seed.firewall import ActionFirewall

            fw = ActionFirewall()  # no rules → DEFAULT-DENY
            decision = await fw.evaluate("parity.unknown.action")
            ok = decision == PolicyDecision.DENY
            return {
                "ok": ok,
                "error": None if ok else f"expected DENY, got {decision!r}",
                "details": {"decision": str(decision)},
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 9. KILL SWITCH CHECK
    # ------------------------------------------------------------------

    async def check_kill_switch(self) -> dict[str, Any]:
        """Verify the kill switch blocks ALL actions when active."""
        try:
            from albra_seed.firewall import KillSwitch

            ks = KillSwitch()
            # Before activation — does not block.
            blocked_before = ks.block("anything")
            await ks.activate(reason="parity-check")
            blocked_after = ks.block("anything")
            await ks.deactivate()
            ok = (not blocked_before) and blocked_after
            return {
                "ok": ok,
                "error": None if ok else "kill switch did not block after activation",
                "details": {"blocked_before": blocked_before, "blocked_after": blocked_after},
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 10. DOCTOR CHECK
    # ------------------------------------------------------------------

    async def check_doctor(self) -> dict[str, Any]:
        """Verify Doctor.diagnose() returns a DiagnosticReport."""
        try:
            from albra_seed.doctor import DiagnosticReport, Doctor

            doctor = Doctor()
            report = await doctor.diagnose()
            ok = isinstance(report, DiagnosticReport)
            return {
                "ok": ok,
                "error": None if ok else "Doctor.diagnose() did not return a DiagnosticReport",
                "details": {
                    "type": type(report).__name__,
                    "components": len(report.components) if ok else 0,
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 11. FEEDBACK CHECK (GAP-1)
    # ------------------------------------------------------------------

    async def check_feedback(self) -> dict[str, Any]:
        """Verify FeedbackManager.record() works (GAP-1)."""
        try:
            from albra_seed.feedback import FeedbackManager, FeedbackType, InMemoryFeedbackStore

            mgr = FeedbackManager(store=InMemoryFeedbackStore())
            fb_id = await mgr.record(
                agent_id="parity-agent",
                feedback_type=FeedbackType.POSITIVE,
                evaluation="parity ok",
                source="parity",
                source_type="system",
            )
            ok = bool(fb_id) and fb_id.startswith("fb_")
            return {
                "ok": ok,
                "error": None if ok else "FeedbackManager.record() did not return a valid fb_ id",
                "details": {"feedback_id": fb_id},
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 12. LEARNING CHECK (GAP-2)
    # ------------------------------------------------------------------

    async def check_learning(self) -> dict[str, Any]:
        """Verify LearningManager.propose() works (GAP-2)."""
        try:
            from albra_seed.learning import LearningManager, LearningProposal, LearningSignal

            mgr = LearningManager()
            proposal = LearningProposal(
                agent_id="parity-agent",
                signal_type=LearningSignal.EXPERIENCE,
                observation="parity probe",
                proposed_change="no change",
                rationale="parity check",
                target_component="other",
            )
            proposal_id = await mgr.propose(proposal)
            ok = bool(proposal_id) and proposal_id.startswith("learn_")
            return {
                "ok": ok,
                "error": None if ok else "LearningManager.propose() did not return a learn_ id",
                "details": {"proposal_id": proposal_id},
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 13. DECISION MEMORY CHECK (GAP-3)
    # ------------------------------------------------------------------

    async def check_decision_memory(self) -> dict[str, Any]:
        """Verify DecisionMemory.record_decision() works (GAP-3)."""
        try:
            from albra_seed.memory import DecisionMemory, DecisionRecord, InMemoryMemoryStore

            dm = DecisionMemory(InMemoryMemoryStore())
            decision = DecisionRecord(
                agent_id="parity-agent",
                objective="parity check",
                decision_taken="run parity",
            )
            decision_id = await dm.record_decision(decision)
            ok = bool(decision_id) and decision_id.startswith("dec_")
            return {
                "ok": ok,
                "error": None if ok else "DecisionMemory.record_decision() did not return a dec_ id",
                "details": {"decision_id": decision_id},
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # 14. SKILL EVOLUTION CHECK (GAP-4)
    # ------------------------------------------------------------------

    async def check_skill_evolution(self) -> dict[str, Any]:
        """Verify SkillEvolutionManager exists and is instantiable (GAP-4)."""
        try:
            from albra_seed.skills import SkillEvolutionManager, SkillRegistry

            registry = SkillRegistry()
            mgr = SkillEvolutionManager(skill_registry=registry)
            # Also exercise a metrics recording to confirm the manager is wired.
            metrics = await mgr.record_execution(
                skill_id="parity-skill",
                success=True,
                duration_ms=10.0,
            )
            ok = (
                hasattr(mgr, "propose_improvement")
                and hasattr(mgr, "apply")
                and metrics.total_executions == 1
            )
            return {
                "ok": ok,
                "error": None if ok else "SkillEvolutionManager missing required methods",
                "details": {
                    "type": type(mgr).__name__,
                    "total_executions": metrics.total_executions,
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "details": {}}

    # ------------------------------------------------------------------
    # SPECIALIZATION DETECTION
    # ------------------------------------------------------------------

    def detect_specialization_components(self) -> list[str]:
        """Return the list of extra specialization modules (modules
        whose dotted name starts with ``specialization.`` AND are not
        in ``ALLOWED_SPECIALIZATIONS``).

        Scans ``sys.modules`` — never imports anything itself. A test
        may inject a fake ``specialization.<name>`` module into
        ``sys.modules`` to verify the detection logic.
        """
        extras: list[str] = []
        seen: set[str] = set()
        allowed = set(ALLOWED_SPECIALIZATIONS)
        for name in list(sys.modules.keys()):
            if not name.startswith("specialization."):
                continue
            if name in seen:
                continue
            seen.add(name)
            if name in allowed:
                # Allowed specializations do NOT count as extras.
                continue
            extras.append(name)
        extras.sort()
        return extras

    # ------------------------------------------------------------------
    # AGGREGATE
    # ------------------------------------------------------------------

    async def run_all(self) -> ParityReport:
        """Run every check and aggregate results into a ParityReport."""
        import_results = self.check_imports()
        classes_results = self.check_classes()

        present = sum(1 for ok in import_results.values() if ok)
        missing = sum(1 for ok in import_results.values() if not ok)
        missing_components = sorted(
            c for c, ok in import_results.items() if not ok
        )

        # Run all behavioral checks.
        behavioral: list[tuple[str, Any]] = [
            ("classes", {"ok": all(classes_results.values()),
                          "error": None if all(classes_results.values()) else "some classes missing",
                          "details": classes_results}),
            ("runtime_birth", await self.check_runtime_birth()),
            ("agent_loop", await self.check_agent_loop()),
            ("memory_dv", await self.check_memory_dv()),
            ("audit", await self.check_audit()),
            ("evidence", await self.check_evidence()),
            ("firewall_default_deny", await self.check_firewall_default_deny()),
            ("kill_switch", await self.check_kill_switch()),
            ("doctor", await self.check_doctor()),
            ("feedback", await self.check_feedback()),
            ("learning", await self.check_learning()),
            ("decision_memory", await self.check_decision_memory()),
            ("skill_evolution", await self.check_skill_evolution()),
        ]

        check_results: dict[str, dict[str, Any]] = {}
        checks_passed = 0
        checks_failed = 0
        for name, result in behavioral:
            check_results[name] = result
            if result.get("ok"):
                checks_passed += 1
            else:
                checks_failed += 1

        specialization_extras = self.detect_specialization_components()
        is_pure_seed = (
            missing == 0
            and checks_failed == 0
            and len(specialization_extras) == 0
        )

        return ParityReport(
            total_components=len(UNIVERSAL_DNA_COMPONENTS),
            present=present,
            missing=missing,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            is_pure_seed=is_pure_seed,
            specialization_components=specialization_extras,
            import_results=import_results,
            check_results=check_results,
            missing_components=missing_components,
        )


__all__ = ["ParityChecker", "ParityReport"]


# Convenience: run_all_sync wrapper for CLI usage.
def run_all_sync(checker: ParityChecker | None = None) -> ParityReport:
    """Synchronous wrapper around ``ParityChecker.run_all()`` for
    use from scripts that are not already inside an event loop."""
    chk = checker or ParityChecker()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(chk.run_all())
    raise RuntimeError(
        "run_all_sync() cannot run inside an existing event loop; "
        "use `await ParityChecker().run_all()` instead."
    )
