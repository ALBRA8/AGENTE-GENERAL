"""Unit tests for the resilience subpackage."""

from __future__ import annotations

import asyncio
import time

import pytest

from albra_seed.resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    FallbackChain,
    GracefulDegradation,
    RetryExecutor,
    RetryPolicy,
    TimeoutExecutor,
    TimeoutExecutorError,
    TimeoutPolicy,
)


# -------------------------------------------------------------------
# 1. RetryExecutor with exponential backoff
# -------------------------------------------------------------------


def test_retry_executor_with_backoff():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    policy = RetryPolicy(
        max_retries=5,
        base_delay_seconds=0.001,
        max_delay_seconds=0.01,
        jitter_fraction=0.0,
    )
    executor = RetryExecutor(policy)
    outcome = asyncio.run(executor.run(flaky))

    assert outcome.success
    assert outcome.value == "ok"
    assert outcome.attempts == 3
    assert len(outcome.errors) == 2  # two errors before success


# -------------------------------------------------------------------
# 1b. RetryExecutor exhausts retries + reports failure
# -------------------------------------------------------------------


def test_retry_executor_exhausts_retries():
    async def always_fail():
        raise RuntimeError("never works")

    policy = RetryPolicy(
        max_retries=2,
        base_delay_seconds=0.001,
        jitter_fraction=0.0,
    )
    executor = RetryExecutor(policy)
    outcome = asyncio.run(executor.run(always_fail))

    assert not outcome.success
    assert outcome.attempts == 3  # 1 + 2 retries
    assert outcome.error is not None
    assert isinstance(outcome.error, RuntimeError)


# -------------------------------------------------------------------
# 2. TimeoutExecutor aborts long call
# -------------------------------------------------------------------


def test_timeout_executor_aborts_long_call():
    async def slow():
        await asyncio.sleep(1.0)
        return "done"

    executor = TimeoutExecutor(TimeoutPolicy(timeout_seconds=0.05))
    with pytest.raises(TimeoutExecutorError):
        asyncio.run(executor.execute(slow, label="slow-op"))


# -------------------------------------------------------------------
# 2b. TimeoutExecutor returns value when call is fast enough
# -------------------------------------------------------------------


def test_timeout_executor_returns_value_when_fast():
    async def fast():
        await asyncio.sleep(0.001)
        return "ok"

    executor = TimeoutExecutor(TimeoutPolicy(timeout_seconds=1.0))
    result = asyncio.run(executor.execute(fast, label="fast-op"))
    assert result == "ok"


# -------------------------------------------------------------------
# 3. FallbackChain returns first success
# -------------------------------------------------------------------


def test_fallback_chain_returns_first_success():
    async def op1():
        raise RuntimeError("fail-1")

    async def op2():
        return "second"

    async def op3():
        return "third"

    chain = FallbackChain([op1, op2, op3], names=["first", "second", "third"])
    outcome = asyncio.run(chain.run())

    assert outcome.success
    assert outcome.value == "second"
    assert outcome.winner_index == 1
    assert outcome.winner_name == "second"
    assert len(outcome.errors) == 1
    assert "fail-1" in outcome.errors[0]


# -------------------------------------------------------------------
# 3b. FallbackChain all-fail returns outcome with errors
# -------------------------------------------------------------------


def test_fallback_chain_all_fail():
    async def op1():
        raise RuntimeError("a")

    async def op2():
        raise ValueError("b")

    chain = FallbackChain([op1, op2])
    outcome = asyncio.run(chain.run())

    assert not outcome.success
    assert outcome.value is None
    assert outcome.winner_index is None
    assert len(outcome.errors) == 2


# -------------------------------------------------------------------
# 4. CircuitBreaker opens after N failures
# -------------------------------------------------------------------


def test_circuit_breaker_opens_after_failures():
    breaker = CircuitBreaker(name="svc", failure_threshold=3, reset_seconds=10.0)
    assert breaker.state == CircuitState.CLOSED

    async def fail():
        raise RuntimeError("svc down")

    # First two failures: still CLOSED (below threshold).
    for _ in range(2):
        with pytest.raises(RuntimeError):
            asyncio.run(breaker.call(fail))
    assert breaker.state == CircuitState.CLOSED

    # Third failure: trips the breaker.
    with pytest.raises(RuntimeError):
        asyncio.run(breaker.call(fail))
    assert breaker.state == CircuitState.OPEN

    # Subsequent calls fail fast.
    with pytest.raises(CircuitBreakerOpenError):
        asyncio.run(breaker.call(fail))


# -------------------------------------------------------------------
# 4b. CircuitBreaker recovers via HALF_OPEN
# -------------------------------------------------------------------


def test_circuit_breaker_half_open_recovery():
    # reset_seconds very short so we can transition OPEN -> HALF_OPEN.
    breaker = CircuitBreaker(name="svc", failure_threshold=2, reset_seconds=0.05)
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("down")
        return "ok"

    # Trip the breaker.
    for _ in range(2):
        with pytest.raises(RuntimeError):
            asyncio.run(breaker.call(flaky))
    assert breaker.state == CircuitState.OPEN

    # Wait for reset_seconds.
    time.sleep(0.06)

    # Next call should transition through HALF_OPEN -> CLOSED on success.
    result = asyncio.run(breaker.call(flaky))
    assert result == "ok"
    assert breaker.state == CircuitState.CLOSED


# -------------------------------------------------------------------
# 5. GracefulDegradation invokes fallback
# -------------------------------------------------------------------


def test_graceful_degradation_invokes_fallback():
    degraded = GracefulDegradation()
    invoked = []

    def fallback(exc: BaseException):
        invoked.append(str(exc))
        return {"degraded": True, "error": str(exc)}

    degraded.register("provider_x", fallback)

    async def failing_call():
        raise RuntimeError("provider unreachable")

    result = asyncio.run(degraded.call_with_fallback("provider_x", failing_call))

    assert result == {"degraded": True, "error": "provider unreachable"}
    assert invoked == ["provider unreachable"]
    assert degraded.is_degraded("provider_x")
    assert degraded.degraded_count("provider_x") == 1


# -------------------------------------------------------------------
# 5b. GracefulDegradation re-raises if no handler
# -------------------------------------------------------------------


def test_graceful_degradation_reraises_when_no_handler():
    degraded = GracefulDegradation()

    async def failing_call():
        raise RuntimeError("oops")

    with pytest.raises(RuntimeError, match="oops"):
        asyncio.run(degraded.call_with_fallback("unknown_component", failing_call))

    assert not degraded.is_degraded("unknown_component")


# -------------------------------------------------------------------
# 5c. GracefulDegradation: fallback raising re-raises original
# -------------------------------------------------------------------


def test_graceful_degradation_fallback_reraises_original():
    degraded = GracefulDegradation()

    def bad_fallback(exc: BaseException):
        raise ValueError("fallback itself broke")

    degraded.register("svc", bad_fallback)

    async def failing_call():
        raise RuntimeError("original")

    # Fallback raised -> original RuntimeError re-raised, not ValueError.
    with pytest.raises(RuntimeError, match="original"):
        asyncio.run(degraded.call_with_fallback("svc", failing_call))
