"""RecoveryAudit — records every recovery action for auditability.

Every recovery decision (which strategy was applied, with which
parameters, on which component, for which exception) is recorded in
the NORMAL audit log AND in the tamper-evident audit log. This ensures
that any auto-recovery action is auditable and tamper-evident.
"""

from __future__ import annotations

from typing import Any

from albra_seed.audit.normal import AuditLog
from albra_seed.audit.tamper_evident import TamperEvidentAuditLog
from albra_seed.recovery.classifier import ErrorClass


class RecoveryAudit:
    """Audit sink for recovery actions.

    Records to BOTH the normal audit log (for queryability) AND the
    tamper-evident audit log (for tamper-evidence). The tamper-evident
    record ensures that the audit log itself cannot be silently
    modified to hide a recovery action.
    """

    def __init__(
        self,
        normal_log: AuditLog | None = None,
        te_log: TamperEvidentAuditLog | None = None,
    ) -> None:
        self._normal = normal_log or AuditLog()
        self._te = te_log or TamperEvidentAuditLog()

    @property
    def normal_log(self) -> AuditLog:
        return self._normal

    @property
    def tamper_evident_log(self) -> TamperEvidentAuditLog:
        return self._te

    def record(
        self,
        *,
        component: str | None,
        action: str,
        error_class: ErrorClass,
        strategy: str,
        success: bool,
        attempts: int = 0,
        error: str | None = None,
        actor: str = "recovery_manager",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a recovery action synchronously to both logs."""
        details = {
            "component": component,
            "error_class": error_class.value,
            "strategy": strategy,
            "success": success,
            "attempts": attempts,
            "error": error,
        }
        if metadata:
            details.update(metadata)
        # NORMAL audit entry
        self._normal.record(
            action=action,
            actor=actor,
            tool=None,
            result="success" if success else "failure",
            error=error,
            metadata=details,
        )
        # TAMPER-EVIDENT audit entry (hash-chained)
        self._te.append({
            "action": action,
            "actor": actor,
            "result": "success" if success else "failure",
            "error": error,
            "metadata": details,
        })

    def verify_integrity(self) -> bool:
        """Verify the tamper-evident chain is intact."""
        return self._te.verify_chain().valid

    def count(self) -> dict[str, int]:
        return {
            "normal": self._normal.count(),
            "tamper_evident": self._te.count(),
        }


__all__ = ["RecoveryAudit"]
