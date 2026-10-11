"""RecoveryManager — orchestrates safe self-healing.

Flow:
  1. Caller invokes `await manager.handle(component, fn)` (or
     `manager.handle_exception(component, exc, fn)` after a failure).
  2. ErrorClassifier classifies the exception.
  3. RecoveryManager looks up the strategy for (component, error_class)
     from its strategy map. If no safe strategy is configured for that
     class, the manager REFUSES to auto-act — it re-raises the original
     exception.
  4. The strategy executes (retry / fallback / restart / degrade).
  5. Every step is recorded in the RecoveryAudit (normal + tamper-
     evident).

CRITICAL SAFETY RULES:
  * SECURITY / UNKNOWN errors are NEVER auto-recovered.
  * The manager NEVER executes "dangerous" actions (file deletion,
    DB wipe, process kill). It only delegates to safe strategies.
  * Every recovery action is audit-logged to BOTH the normal audit
    log AND the tamper-evident audit log.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from albra_seed.recovery.audit import RecoveryAudit
from albra_seed.recovery.classifier import ErrorClass, ErrorClassifier
from albra_seed.recovery.strategies import (
    DEFAULT_STRATEGY_BY_CLASS,
    GracefulDegradationStrategy,
    RecoveryStrategy,
    RetryStrategy,
)


# Error classes that NEVER trigger auto-recovery. The manager re-raises
# the original exception for these classes.
_NEVER_AUTO_RECOVER: frozenset[ErrorClass] = frozenset({
    ErrorClass.SECURITY,
    ErrorClass.UNKNOWN,
})

# Substring markers of "dangerous" actions — the manager refuses to
# invoke a callable whose name (or strategy name) matches these
# patterns, even if explicitly configured. Defense-in-depth.
_DANGEROUS_MARKERS: tuple[str, ...] = (
    "rm_rf", "delete_database", "drop_table", "truncate", "wipe",
    "kill_process", "force_kill", "shutdown_host", "format_disk",
    "delete_all", "purge",
)


class RecoveryManager:
    """Detects errors, classifies them, and executes safe recovery.

    Strategies are registered per (component, error_class). A default
    fallback map (DEFAULT_STRATEGY_BY_CLASS) is used for components
    without an explicit registration.
    """

    def __init__(
        self,
        *,
        classifier: ErrorClassifier | None = None,
        audit: RecoveryAudit | None = None,
        default_retry: RetryStrategy | None = None,
    ) -> None:
        self._classifier = classifier or ErrorClassifier()
        self._audit = audit or RecoveryAudit()
        self._default_retry = default_retry or RetryStrategy(
            max_attempts=2, base_delay_seconds=0.01
        )
        # Per-component strategy overrides
        self._strategies: dict[tuple[str, ErrorClass], RecoveryStrategy] = {}
        self._dangerous_blacklist: set[str] = set()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def register_strategy(
        self,
        component: str,
        error_class: ErrorClass,
        strategy: RecoveryStrategy,
    ) -> None:
        self._check_dangerous(strategy.name)
        self._strategies[(component, error_class)] = strategy

    def blacklist_action(self, action_name: str) -> None:
        """Mark an action name as too dangerous to ever auto-invoke."""
        self._dangerous_blacklist.add(action_name)

    def _check_dangerous(self, name: str) -> None:
        lname = name.lower()
        for marker in _DANGEROUS_MARKERS:
            if marker in lname:
                raise ValueError(
                    f"Refusing to register dangerous strategy/action '{name}' "
                    f"(matches dangerous marker '{marker}')."
                )
        if name in self._dangerous_blacklist:
            raise ValueError(f"Action '{name}' is blacklisted.")

    # ------------------------------------------------------------------
    # Core entry point
    # ------------------------------------------------------------------
    async def handle(
        self,
        component: str,
        fn: Callable[[], Awaitable[Any]],
        *,
        action: str = "component.invoke",
    ) -> Any:
        """Invoke `fn`; if it raises, classify + (maybe) recover.

        If the error is SECURITY/UNKNOWN or no safe strategy is
        available, the original exception is re-raised.
        """
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            return await self._handle_exception(component, exc, fn, action=action)

    async def handle_exception(
        self,
        component: str,
        exc: BaseException,
        fn: Callable[[], Awaitable[Any]] | None = None,
        *,
        action: str = "component.invoke",
    ) -> Any:
        """Recover from an already-thrown exception.

        If `fn` is None, only the audit + classification happens; no
        re-execution. The original exception is re-raised in that case.
        """
        return await self._handle_exception(component, exc, fn, action=action)

    async def _handle_exception(
        self,
        component: str,
        exc: BaseException,
        fn: Callable[[], Awaitable[Any]] | None,
        *,
        action: str,
    ) -> Any:
        error_class = self._classifier.classify(exc)

        # SECURITY / UNKNOWN: NEVER auto-recover — re-raise + audit.
        if error_class in _NEVER_AUTO_RECOVER:
            self._audit.record(
                component=component,
                action=f"{action}.recovery_refused",
                error_class=error_class,
                strategy="none",
                success=False,
                error=str(exc),
            )
            raise exc

        strategy = self._strategies.get((component, error_class))
        if strategy is None:
            # Use the default per-class strategy.
            kind = DEFAULT_STRATEGY_BY_CLASS.get(error_class, "none")
            if kind == "retry":
                strategy = self._default_retry
            elif kind == "graceful_degradation":
                strategy = GracefulDegradationStrategy(default_value=None)
            else:
                # No safe default — re-raise + audit.
                self._audit.record(
                    component=component,
                    action=f"{action}.recovery_refused",
                    error_class=error_class,
                    strategy="none",
                    success=False,
                    error=str(exc),
                )
                raise exc

        if fn is None:
            # Strategy needs fn but caller didn't provide one.
            self._audit.record(
                component=component,
                action=f"{action}.recovery_skipped",
                error_class=error_class,
                strategy=strategy.name,
                success=False,
                error="no fn provided to re-invoke",
            )
            raise exc

        # Execute the strategy.
        try:
            result = await strategy.execute(fn, exc, component=component)
            self._audit.record(
                component=component,
                action=f"{action}.recovered",
                error_class=error_class,
                strategy=strategy.name,
                success=True,
            )
            return result
        except Exception as recovery_exc:  # noqa: BLE001
            self._audit.record(
                component=component,
                action=f"{action}.recovery_failed",
                error_class=error_class,
                strategy=strategy.name,
                success=False,
                error=str(recovery_exc),
            )
            raise recovery_exc

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def classify(self, exc: BaseException) -> ErrorClass:
        return self._classifier.classify(exc)

    def audit(self) -> RecoveryAudit:
        return self._audit

    def verify_audit_integrity(self) -> bool:
        return self._audit.verify_integrity()


__all__ = ["RecoveryManager"]
