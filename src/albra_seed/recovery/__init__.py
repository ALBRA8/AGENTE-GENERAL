"""Recovery subpackage — safe self-healing for the SEED.

Public API:
    RecoveryManager    — orchestrates classification + strategy.
    ErrorClassifier    — maps exceptions to ErrorClass.
    ErrorClass         — transient / persistent / security / resource /
                          external / unknown.
    RecoveryStrategy   — abstract base for strategies.
    RetryStrategy      — exponential backoff retry.
    FallbackStrategy   — invoke a fallback callable.
    RestartComponentStrategy — call a registered restart hook.
    GracefulDegradationStrategy — return a safe default.
    RecoveryAudit      — records every recovery action (normal + tamper-
                          evident).

CRITICAL SAFETY RULES:
  * SECURITY / UNKNOWN errors are NEVER auto-recovered.
  * The manager NEVER executes dangerous actions (file deletion,
    DB wipe, process kill). It only delegates to safe strategies.
  * Every recovery action is audit-logged to BOTH the normal audit
    log AND the tamper-evident audit log.
"""

from albra_seed.recovery.audit import RecoveryAudit
from albra_seed.recovery.classifier import ErrorClass, ErrorClassifier
from albra_seed.recovery.manager import RecoveryManager
from albra_seed.recovery.strategies import (
    DEFAULT_STRATEGY_BY_CLASS,
    FallbackStrategy,
    GracefulDegradationStrategy,
    RecoveryResult,
    RecoveryStrategy,
    RestartComponentStrategy,
    RetryStrategy,
)

__all__ = [
    "RecoveryManager",
    "ErrorClassifier",
    "ErrorClass",
    "RecoveryStrategy",
    "RecoveryResult",
    "RetryStrategy",
    "FallbackStrategy",
    "RestartComponentStrategy",
    "GracefulDegradationStrategy",
    "DEFAULT_STRATEGY_BY_CLASS",
    "RecoveryAudit",
]
