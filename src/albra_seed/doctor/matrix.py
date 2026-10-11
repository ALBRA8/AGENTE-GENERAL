"""DiagnosticMatrix — COMPONENT × STATUS × IMPLEMENTATION × TEST ×
DEPENDENCY × RISK × EVIDENCE × NOTES (per spec section 69).

A flat, serializable matrix describing every component along eight
orthogonal axes. Used as the canonical "what is real right now?"
answer for the seed.

This is INTENTIONALLY separate from `DiagnosticReport` (which is the
runtime-rolled-up per-run output). The matrix is the *spec*; the
report is the *observation*.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import HealthState
from albra_seed.doctor.report import ImplementationStatus


class MatrixRow(BaseModel):
    """One row of the diagnostic matrix."""

    component: str
    status: HealthState = HealthState.NOT_VERIFIED
    implementation: ImplementationStatus = ImplementationStatus.NOT_VERIFIED
    test: str = "not run"  # what test/exercise was used to verify
    dependency: str = "none"  # external dependency (if any)
    risk: str = "low"  # low | medium | high | critical
    evidence: list[str] = Field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "status": self.status.value,
            "implementation": self.implementation.value,
            "test": self.test,
            "dependency": self.dependency,
            "risk": self.risk,
            "evidence": list(self.evidence),
            "notes": self.notes,
        }


# The full matrix is intentionally a static description of the seed
# itself. It is regenerated for each new release. Components marked
# EXTERNAL_DEPENDENCY rely on optional third-party packages (openai,
# anthropic, hvac, opentelemetry, chromadb) which are NOT required in
# CORE_ONLY mode.
_DEFAULT_ROWS: list[dict[str, Any]] = [
    {"component": "runtime", "implementation": "IMPLEMENTED", "test": "runtime_check", "dependency": "none", "risk": "low"},
    {"component": "memory.inmemory", "implementation": "IMPLEMENTED", "test": "memory_check", "dependency": "none", "risk": "low"},
    {"component": "memory.sqlite", "implementation": "IMPLEMENTED", "test": "import albra_seed.memory.sqlite_store", "dependency": "stdlib sqlite3", "risk": "low"},
    {"component": "memory.vector", "implementation": "EXTERNAL_DEPENDENCY", "test": "VectorMemoryStore import", "dependency": "chromadb", "risk": "medium"},
    {"component": "providers.mock", "implementation": "IMPLEMENTED", "test": "providers_check", "dependency": "none", "risk": "low"},
    {"component": "providers.openai", "implementation": "EXTERNAL_DEPENDENCY", "test": "OpenAIProvider import", "dependency": "openai", "risk": "medium"},
    {"component": "providers.anthropic", "implementation": "EXTERNAL_DEPENDENCY", "test": "AnthropicProvider import", "dependency": "anthropic", "risk": "medium"},
    {"component": "providers.router", "implementation": "IMPLEMENTED", "test": "providers_check", "dependency": "none", "risk": "low"},
    {"component": "context.correlation", "implementation": "IMPLEMENTED", "test": "CorrelationTracker.snapshot", "dependency": "none", "risk": "low"},
    {"component": "reasoning", "implementation": "IMPLEMENTED", "test": "DefaultReasoningEngine.decompose", "dependency": "none", "risk": "low"},
    {"component": "tools.registry", "implementation": "IMPLEMENTED", "test": "tools_check", "dependency": "none", "risk": "low"},
    {"component": "tools.executor", "implementation": "IMPLEMENTED", "test": "ToolExecutor dispatch", "dependency": "none", "risk": "low"},
    {"component": "capabilities", "implementation": "IMPLEMENTED", "test": "CapabilityRegistry", "dependency": "none", "risk": "low"},
    {"component": "skills", "implementation": "IMPLEMENTED", "test": "skills_check", "dependency": "none", "risk": "low"},
    {"component": "plugins", "implementation": "IMPLEMENTED", "test": "PluginManager import", "dependency": "none", "risk": "medium"},
    {"component": "security.permissions", "implementation": "IMPLEMENTED", "test": "security_check", "dependency": "none", "risk": "high"},
    {"component": "security.secrets.env", "implementation": "IMPLEMENTED", "test": "EnvSecretsManager.get", "dependency": "none", "risk": "high"},
    {"component": "security.secrets.vault", "implementation": "EXTERNAL_DEPENDENCY", "test": "VaultSecretsManager init", "dependency": "hvac", "risk": "high"},
    {"component": "firewall", "implementation": "IMPLEMENTED", "test": "security_check", "dependency": "none", "risk": "high"},
    {"component": "kill_switch", "implementation": "IMPLEMENTED", "test": "KillSwitch.activate", "dependency": "none", "risk": "critical"},
    {"component": "policy.engine", "implementation": "IMPLEMENTED", "test": "PolicyEngine.evaluate", "dependency": "none", "risk": "high"},
    {"component": "autonomy.manager", "implementation": "IMPLEMENTED", "test": "AutonomyManager.can_perform", "dependency": "none", "risk": "high"},
    {"component": "approvals.engine", "implementation": "IMPLEMENTED", "test": "ApprovalEngine.request", "dependency": "none", "risk": "high"},
    {"component": "approvals.evidence", "implementation": "IMPLEMENTED", "test": "ApprovalEvidenceStore.verify_chain", "dependency": "none", "risk": "high"},
    {"component": "evidence.manager", "implementation": "IMPLEMENTED", "test": "EvidenceManager.collect", "dependency": "none", "risk": "high"},
    {"component": "evidence.integrity", "implementation": "IMPLEMENTED", "test": "EvidenceIntegrity.verify", "dependency": "none", "risk": "high"},
    {"component": "provenance.tracker", "implementation": "IMPLEMENTED", "test": "ProvenanceTracker.track", "dependency": "none", "risk": "medium"},
    {"component": "execution.runner", "implementation": "IMPLEMENTED", "test": "ExecutionRunner.run", "dependency": "none", "risk": "medium"},
    {"component": "execution.idempotency", "implementation": "IMPLEMENTED", "test": "IdempotencyManager", "dependency": "none", "risk": "medium"},
    {"component": "execution.replay", "implementation": "IMPLEMENTED", "test": "TraceReplay", "dependency": "none", "risk": "medium"},
    {"component": "workflows", "implementation": "IMPLEMENTED", "test": "WorkflowExecutor.execute", "dependency": "none", "risk": "medium"},
    {"component": "jobs.manager", "implementation": "IMPLEMENTED", "test": "JobManager.submit", "dependency": "none", "risk": "medium"},
    {"component": "scheduler", "implementation": "IMPLEMENTED", "test": "Scheduler.tick", "dependency": "croniter (optional)", "risk": "low"},
    {"component": "resilience.retry", "implementation": "IMPLEMENTED", "test": "RetryExecutor", "dependency": "none", "risk": "low"},
    {"component": "resilience.circuit_breaker", "implementation": "IMPLEMENTED", "test": "CircuitBreaker transitions", "dependency": "none", "risk": "low"},
    {"component": "observability.logger", "implementation": "IMPLEMENTED", "test": "StructuredLogger emits JSON", "dependency": "structlog", "risk": "low"},
    {"component": "observability.metrics", "implementation": "IMPLEMENTED", "test": "MetricsRegistry.snapshot", "dependency": "none", "risk": "low"},
    {"component": "observability.tracer", "implementation": "IMPLEMENTED", "test": "Tracer.start_span/end_span", "dependency": "none", "risk": "low"},
    {"component": "observability.health", "implementation": "IMPLEMENTED", "test": "HealthMonitor.snapshot", "dependency": "none", "risk": "low"},
    {"component": "observability.readiness", "implementation": "IMPLEMENTED", "test": "ReadinessProbe.ready", "dependency": "none", "risk": "low"},
    {"component": "audit.normal", "implementation": "IMPLEMENTED", "test": "AuditLog.record", "dependency": "none", "risk": "high"},
    {"component": "audit.tamper_evident", "implementation": "IMPLEMENTED", "test": "TamperEvidentAuditLog.verify_chain", "dependency": "none", "risk": "high"},
    {"component": "audit.verifier", "implementation": "IMPLEMENTED", "test": "ChainVerifier.detect_tampering", "dependency": "none", "risk": "high"},
    {"component": "doctor", "implementation": "IMPLEMENTED", "test": "Doctor.diagnose", "dependency": "none", "risk": "medium"},
    {"component": "recovery.manager", "implementation": "IMPLEMENTED", "test": "RecoveryManager.handle", "dependency": "none", "risk": "high"},
    {"component": "mcp", "implementation": "NOT_CONFIGURED", "test": "import albra_seed.mcp", "dependency": "optional", "risk": "low"},
    {"component": "communication", "implementation": "NOT_CONFIGURED", "test": "import albra_seed.communication", "dependency": "optional", "risk": "low"},
]


class DiagnosticMatrix(BaseModel):
    """Component × Status × Implementation × Test × Dependency ×
    Risk × Evidence × Notes matrix (spec section 69)."""

    rows: list[MatrixRow] = Field(default_factory=list)

    @classmethod
    def default(cls) -> "DiagnosticMatrix":
        """Build the default matrix from `_DEFAULT_ROWS`.

        Status defaults to NOT_VERIFIED because the matrix is a STATIC
        spec — only the Doctor (running checks) can upgrade a row to
        HEALTHY+IMPLEMENTED.
        """
        rows: list[MatrixRow] = []
        for r in _DEFAULT_ROWS:
            impl_str = r.get("implementation", "NOT_VERIFIED")
            try:
                impl = ImplementationStatus(impl_str.lower())
            except ValueError:
                impl = ImplementationStatus.NOT_VERIFIED
            rows.append(MatrixRow(
                component=r["component"],
                status=HealthState.NOT_VERIFIED,
                implementation=impl,
                test=r.get("test", ""),
                dependency=r.get("dependency", "none"),
                risk=r.get("risk", "low"),
                evidence=[],
                notes=r.get("notes", ""),
            ))
        return cls(rows=rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "axes": [
                "component",
                "status",
                "implementation",
                "test",
                "dependency",
                "risk",
                "evidence",
                "notes",
            ],
            "rows": [r.to_dict() for r in self.rows],
            "row_count": len(self.rows),
        }

    def components(self) -> list[str]:
        return [r.component for r in self.rows]


__all__ = ["DiagnosticMatrix", "MatrixRow"]
