"""Resilience subpackage — retry, timeout, fallback, circuit breaker, degradation.

Public API:
    RetryPolicy / RetryExecutor        — exponential backoff with jitter
    TimeoutPolicy / TimeoutExecutor    — async-call timeout wrapper
    FallbackChain                      — first-success-wins chain
    CircuitBreaker                     — CLOSED/OPEN/HALF_OPEN state machine
    GracefulDegradation                — per-component fallback handlers
"""

from albra_seed.resilience.retry import RetryExecutor, RetryOutcome, RetryPolicy
from albra_seed.resilience.timeout import (
    TimeoutExecutor,
    TimeoutExecutorError,
    TimeoutPolicy,
)
from albra_seed.resilience.fallback import FallbackChain, FallbackOutcome
from albra_seed.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)
from albra_seed.resilience.degradation import (
    FallbackHandler,
    GracefulDegradation,
)

__all__ = [
    "RetryPolicy",
    "RetryExecutor",
    "RetryOutcome",
    "TimeoutPolicy",
    "TimeoutExecutor",
    "TimeoutExecutorError",
    "FallbackChain",
    "FallbackOutcome",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitState",
    "GracefulDegradation",
    "FallbackHandler",
]
