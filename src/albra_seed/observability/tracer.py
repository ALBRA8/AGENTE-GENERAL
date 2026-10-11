"""Tracer — lightweight span-based tracing.

Spans form a tree (parent_span / children). Each span has:
  * name, start_time, end_time, duration_ms
  * optional tags (free-form dict)
  * optional parent span

`Tracer.start_span(name, parent=None)` opens a span; `end_span(span)`
closes it and records duration. Spans are kept in-process; an optional
OTLP exporter can be configured to flush to a remote collector via
`opentelemetry` (lazy import — never required at module load).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from albra_seed.context.correlation import CorrelationTracker


def _now_ns() -> int:
    return time.time_ns()


class Span:
    """A single trace span."""

    def __init__(self, name: str, parent: "Span | None" = None, *, trace_id: str | None = None) -> None:
        self.span_id: str = uuid.uuid4().hex
        self.name: str = name
        self.parent: Span | None = parent
        self.parent_span_id: str | None = parent.span_id if parent else None
        self.trace_id: str = trace_id or (parent.trace_id if parent else uuid.uuid4().hex)
        self.tags: dict[str, Any] = {}
        self.start_ns: int = _now_ns()
        self.end_ns: int | None = None
        self.duration_ms: float | None = None
        self.status: str = "open"
        self.error: str | None = None

    def set_tag(self, key: str, value: Any) -> None:
        self.tags[key] = value

    def set_error(self, err: BaseException | str) -> None:
        self.error = str(err)
        self.status = "error"

    def finish(self) -> None:
        if self.end_ns is not None:
            return  # already finished
        self.end_ns = _now_ns()
        self.duration_ms = (self.end_ns - self.start_ns) / 1_000_000.0
        if self.status == "open":
            self.status = "closed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "trace_id": self.trace_id,
            "name": self.name,
            "tags": dict(self.tags),
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
        }


class Tracer:
    """In-process span tracer with optional OTLP export.

    Usage:
        tracer = Tracer()
        root = tracer.start_span("agent.run")
        child = tracer.start_span("llm.generate", parent=root)
        ...
        tracer.end_span(child)
        tracer.end_span(root)
        spans = tracer.collect()  # all spans ever opened on this tracer
    """

    def __init__(self, *, correlation_id: str | None = None) -> None:
        self._spans: list[Span] = []
        self._correlation_id: str | None = correlation_id
        # OTLP exporter is configured but NOT instantiated until export()
        # is called — keeps opentelemetry lazy.
        self._otlp_endpoint: str | None = None
        self._otlp_service_name: str = "albra-seed"

    def configure_otlp(self, endpoint: str, service_name: str = "albra-seed") -> None:
        """Configure (but don't yet instantiate) the OTLP exporter."""
        self._otlp_endpoint = endpoint
        self._otlp_service_name = service_name

    def start_span(self, name: str, parent: Span | None = None, **tags: Any) -> Span:
        span = Span(name, parent=parent)
        # Tag with current correlation_id so traces join logs / audit entries.
        cid = self._correlation_id or CorrelationTracker.current_correlation_id()
        span.set_tag("correlation_id", cid)
        exec_id = CorrelationTracker.current_execution_id()
        if exec_id:
            span.set_tag("execution_id", exec_id)
        for k, v in tags.items():
            span.set_tag(k, v)
        self._spans.append(span)
        return span

    def end_span(self, span: Span, *, error: BaseException | str | None = None) -> Span:
        if error is not None:
            span.set_error(error)
        span.finish()
        return span

    def collect(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._spans]

    def root_spans(self) -> list[Span]:
        return [s for s in self._spans if s.parent is None]

    def children(self, parent: Span) -> list[Span]:
        return [s for s in self._spans if s.parent is parent]

    def clear(self) -> None:
        self._spans.clear()

    # ------------------------------------------------------------------
    # OTLP export (lazy)
    # ------------------------------------------------------------------
    def export(self) -> dict[str, Any]:
        """Export collected spans to OTLP if configured.

        Returns a small status dict. If opentelemetry is not installed or
        no endpoint is configured, returns `{"exported": 0, "reason": ...}`
        without raising.
        """
        if not self._otlp_endpoint:
            return {"exported": 0, "reason": "no_endpoint_configured"}
        try:
            # Lazy import — never required at module load.
            from opentelemetry import trace as ot_trace  # type: ignore[import-untyped]
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-untyped]
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource  # type: ignore[import-untyped]
            from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]
            from opentelemetry.sdk.trace.export import (  # type: ignore[import-untyped]
                BatchSpanProcessor,
            )
            from opentelemetry.trace import SpanKind  # type: ignore[import-untyped]
        except ImportError as exc:
            return {"exported": 0, "reason": f"opentelemetry_not_installed: {exc}"}

        resource = Resource.create({"service.name": self._otlp_service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=self._otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        ot_tracer = provider.get_tracer("albra-seed")
        exported = 0
        for span in self._spans:
            with ot_tracer.start_as_current_span(
                span.name,
                start_time=span.start_ns,
                attributes=span.tags,
            ) as ot_span:
                if span.status == "error":
                    ot_span.set_status(ot_trace.Status(ot_trace.StatusCode.ERROR))
                if span.error:
                    ot_span.record_exception(RuntimeError(span.error))
                ot_span.end(end_time=span.end_ns or _now_ns())
                exported += 1
        return {"exported": exported, "endpoint": self._otlp_endpoint}


__all__ = ["Tracer", "Span"]
