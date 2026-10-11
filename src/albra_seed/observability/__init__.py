"""Observability subpackage — structured logs, metrics, traces, health.

Public API:
    StructuredLogger  — structlog-backed JSON/console logger with
                         correlation_id propagation + secret redaction.
    MetricsRegistry   — counters, gauges, histograms (tag-aware).
    Tracer / Span     — lightweight span tracer with optional OTLP export.
    HealthMonitor     — per-component health tracker.
    ReadinessProbe    — readiness gate over critical components.

DESIGN PRINCIPLES:
  * NO domain knowledge — these are generic observability primitives.
  * Secrets NEVER logged (see `logger.redact`).
  * Correlation propagated via contextvars (reuses CorrelationTracker).
  * Optional deps (opentelemetry, structlog) are lazy where possible.
"""

from albra_seed.observability.logger import StructuredLogger, redact
from albra_seed.observability.metrics import MetricsRegistry
from albra_seed.observability.tracer import Span, Tracer
from albra_seed.observability.health import HealthMonitor, HealthReport
from albra_seed.observability.readiness import ReadinessProbe

__all__ = [
    "StructuredLogger",
    "redact",
    "MetricsRegistry",
    "Tracer",
    "Span",
    "HealthMonitor",
    "HealthReport",
    "ReadinessProbe",
]
