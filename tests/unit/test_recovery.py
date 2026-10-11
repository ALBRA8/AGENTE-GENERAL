"""Unit tests for the recovery subpackage (F2-G)."""

from __future__ import annotations

import asyncio

import pytest

from albra_seed.recovery import (
    ErrorClass,
    ErrorClassifier,
    FallbackStrategy,
    GracefulDegradationStrategy,
    RecoveryAudit,
    RecoveryManager,
    RestartComponentStrategy,
    RetryStrategy,
)


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# Error classifier
# =====================================================================


def test_recovery_manager_classifies_error():
    """ErrorClassifier maps known exception patterns to ErrorClass values."""
    clf = ErrorClassifier()

    # Transient: timeout / connection / rate-limit.
    assert clf.classify(TimeoutError("operation timed out")) == ErrorClass.TRANSIENT
    assert clf.classify(ConnectionError("connection refused")) == ErrorClass.TRANSIENT

    # Security: permission denied / unauthorized.
    assert clf.classify(PermissionError("permission denied")) == ErrorClass.SECURITY
    assert clf.classify(RuntimeError("unauthorized access")) == ErrorClass.SECURITY

    # Resource: out of memory / disk full.
    assert clf.classify(MemoryError("out of memory")) == ErrorClass.RESOURCE

    # Unknown: an exception with no recognizable pattern.
    assert clf.classify(RuntimeError("something weird")) == ErrorClass.UNKNOWN

    # RecoveryManager exposes the classifier too.
    mgr = RecoveryManager()
    assert mgr.classify(TimeoutError("timed out")) == ErrorClass.TRANSIENT


# =====================================================================
# RetryStrategy
# =====================================================================


def test_retry_strategy_executes():
    """RetryStrategy re-invokes the callable up to max_attempts."""

    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset")
        return "ok"

    strategy = RetryStrategy(max_attempts=5, base_delay_seconds=0.001)
    result = _run(strategy.execute(flaky, ConnectionError("initial")))
    assert result == "ok"
    assert calls["n"] == 3


def test_retry_strategy_exhausts_and_raises():
    """RetryStrategy raises the last exception when max_attempts is exhausted."""

    calls = {"n": 0}

    async def always_fail():
        calls["n"] += 1
        raise ConnectionError("always fails")

    strategy = RetryStrategy(max_attempts=2, base_delay_seconds=0.001)
    with pytest.raises(ConnectionError):
        _run(strategy.execute(always_fail, ConnectionError("initial")))
    # Two attempts inside the strategy (the initial call already failed
    # before strategy.execute was entered).
    assert calls["n"] == 2


def test_fallback_strategy_returns_fallback():
    """FallbackStrategy invokes the fallback callable and returns its result."""

    async def fallback():
        return "fallback_value"

    async def primary():
        raise ValueError("nope")

    strategy = FallbackStrategy(fallback=fallback)
    result = _run(strategy.execute(primary, ValueError("nope")))
    assert result == "fallback_value"


def test_restart_component_strategy_invokes_hook():
    """RestartComponentStrategy calls the restart hook then retries fn."""

    restarted = {"called": False}

    async def restart_hook(component):
        restarted["called"] = True
        restarted["component"] = component

    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        # First call (after restart) succeeds.
        return "recovered"

    strategy = RestartComponentStrategy(restart_hook=restart_hook)
    result = _run(strategy.execute(fn, RuntimeError("init"), component="my_component"))
    assert result == "recovered"
    assert restarted["called"] is True
    assert restarted["component"] == "my_component"


def test_graceful_degradation_strategy_returns_default():
    """GracefulDegradationStrategy returns the default without re-invoking fn."""

    async def fn():
        raise AssertionError("should not be called")

    strategy = GracefulDegradationStrategy(default_value="degraded_default")
    result = _run(strategy.execute(fn, RuntimeError("x")))
    assert result == "degraded_default"


# =====================================================================
# Recovery audit
# =====================================================================


def test_recovery_audit_records_action():
    """RecoveryAudit.record writes to BOTH the normal audit log AND the
    tamper-evident audit log."""
    audit = RecoveryAudit()
    audit.record(
        component="my_component",
        action="component.invoke.recovered",
        error_class=ErrorClass.TRANSIENT,
        strategy="retry",
        success=True,
        attempts=3,
    )
    counts = audit.count()
    assert counts["normal"] == 1
    assert counts["tamper_evident"] == 1
    # The tamper-evident chain verifies as intact.
    assert audit.verify_integrity() is True

    # The normal audit entry contains the recovery metadata.
    entries = audit.normal_log.all()
    assert len(entries) == 1
    e = entries[0]
    assert e.action == "component.invoke.recovered"
    assert e.metadata["component"] == "my_component"
    assert e.metadata["strategy"] == "retry"
    assert e.metadata["success"] is True


def test_recovery_audit_detects_tampering():
    """Tampering with a recovery audit record breaks the chain."""
    audit = RecoveryAudit()
    audit.record(component="c1", action="a1", error_class=ErrorClass.TRANSIENT,
                 strategy="retry", success=True)
    audit.record(component="c2", action="a2", error_class=ErrorClass.EXTERNAL,
                 strategy="retry", success=True)
    assert audit.verify_integrity() is True

    # Tamper with the first TE record's action.
    te_entries = audit.tamper_evident_log.all()
    te_entries[0].action = "TAMPERED"
    assert audit.verify_integrity() is False


# =====================================================================
# RecoveryManager safety — NEVER auto-recover dangerous actions
# =====================================================================


def test_recovery_does_not_run_dangerous_actions():
    """The RecoveryManager refuses to register strategies whose names
    match dangerous patterns (rm_rf, delete_database, etc.)."""
    mgr = RecoveryManager()

    # Refuses to register a strategy with a dangerous name.
    dangerous = GracefulDegradationStrategy(default_value=None)
    dangerous.name = "rm_rf_everything"
    with pytest.raises(ValueError, match="dangerous"):
        mgr.register_strategy("my_component", ErrorClass.PERSISTENT, dangerous)

    # Refuses to register with another dangerous marker.
    dangerous2 = GracefulDegradationStrategy(default_value=None)
    dangerous2.name = "delete_database"
    with pytest.raises(ValueError):
        mgr.register_strategy("my_component", ErrorClass.PERSISTENT, dangerous2)

    # Blacklist also blocks registration.
    mgr.blacklist_action("custom_dangerous_action")
    dangerous3 = GracefulDegradationStrategy(default_value=None)
    dangerous3.name = "custom_dangerous_action"
    with pytest.raises(ValueError, match="blacklisted"):
        mgr.register_strategy("my_component", ErrorClass.PERSISTENT, dangerous3)


def test_recovery_does_not_auto_recover_security_errors():
    """SECURITY / UNKNOWN errors are NEVER auto-recovered — the original
    exception is re-raised and the audit records a refusal."""
    mgr = RecoveryManager()

    async def fn():
        return "should never reach"

    # SECURITY: PermissionError → re-raised, audit records refusal.
    with pytest.raises(PermissionError):
        _run(mgr.handle_exception("comp", PermissionError("denied"), fn))
    audit_entries = mgr.audit().normal_log.all()
    assert len(audit_entries) == 1
    assert "refused" in audit_entries[0].action
    assert audit_entries[0].metadata["error_class"] == "security"
    assert audit_entries[0].metadata["strategy"] == "none"

    # UNKNOWN: a weird RuntimeError with no recognizable pattern.
    with pytest.raises(RuntimeError):
        _run(mgr.handle_exception("comp", RuntimeError("totally novel"), fn))


def test_recovery_retries_transient_errors():
    """RecoveryManager auto-retries TRANSIENT errors via RetryStrategy."""
    mgr = RecoveryManager()
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset")
        return "ok"

    result = _run(mgr.handle("my_component", flaky))
    assert result == "ok"
    # Audit records a successful recovery.
    audit_entries = mgr.audit().normal_log.all()
    assert any("recovered" in e.action and e.metadata["strategy"] == "retry"
               for e in audit_entries)


def test_recovery_manager_uses_custom_strategy():
    """RecoveryManager uses a registered custom strategy for the
    (component, error_class) pair."""
    mgr = RecoveryManager()

    async def fallback():
        return "fallback_result"

    mgr.register_strategy(
        "my_comp",
        ErrorClass.PERSISTENT,
        FallbackStrategy(fallback=fallback),
    )

    async def primary():
        raise ValueError("persistent error")

    result = _run(mgr.handle("my_comp", primary))
    assert result == "fallback_result"
    audit_entries = mgr.audit().normal_log.all()
    assert any(e.metadata["strategy"] == "fallback" for e in audit_entries)
