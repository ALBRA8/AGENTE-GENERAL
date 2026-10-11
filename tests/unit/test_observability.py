"""Unit tests for the observability subpackage (F2-G)."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from albra_seed.observability import (
    HealthMonitor,
    MetricsRegistry,
    ReadinessProbe,
    StructuredLogger,
    Tracer,
)
from albra_seed.observability.logger import redact
from albra_seed.core.types import HealthState


# =====================================================================
# Structured logger
# =====================================================================


def test_structured_logger_emits_json():
    """StructuredLogger in JSON mode emits one JSON object per line
    including the event name + level."""
    buf = io.StringIO()
    logger = StructuredLogger(name="test", format="json", stream=buf)
    logger.info("test.event", key="value", number=42)
    out = buf.getvalue().strip()
    assert out, "logger produced no output"
    line = json.loads(out)
    assert line["event"] == "test.event"
    assert line["level"] == "info"
    assert line["key"] == "value"
    assert line["number"] == 42
    assert line["logger"] == "test"
    # Correlation id is auto-injected (may be fresh per test).
    assert "correlation_id" in line


# =====================================================================
# Metrics registry
# =====================================================================


def test_metrics_registry_counters():
    """MetricsRegistry.inc accumulates per-tag values; snapshot returns them."""
    reg = MetricsRegistry()
    reg.inc("requests_total")
    reg.inc("requests_total", 4)
    reg.inc("requests_total", tags={"route": "/a"})
    reg.inc("requests_total", tags={"route": "/a"})

    reg.set("queue_depth", 7)
    reg.observe("latency_ms", 12.5)
    reg.observe("latency_ms", 25.0)

    snap = reg.snapshot()
    counter_points = {(c["name"], frozenset(c["tags"].items())): c["value"] for c in snap["counters"]}
    # Untagged total = 1 + 4 = 5
    assert counter_points[("requests_total", frozenset())] == 5.0
    # /a route total = 1 + 1 = 2
    assert counter_points[("requests_total", frozenset({("route", "/a")}))] == 2.0

    gauges = snap["gauges"]
    assert any(g["name"] == "queue_depth" and g["value"] == 7.0 for g in gauges)

    hist = snap["histograms"][0]
    assert hist["name"] == "latency_ms"
    assert hist["count"] == 2
    assert hist["sum"] == 37.5


# =====================================================================
# Tracer
# =====================================================================


def test_tracer_spans_have_parent():
    """Tracer.start_span(parent=...) links the child to the parent."""
    tracer = Tracer()
    root = tracer.start_span("root")
    child = tracer.start_span("child", parent=root)
    grandchild = tracer.start_span("grandchild", parent=child)

    tracer.end_span(grandchild)
    tracer.end_span(child)
    tracer.end_span(root)

    spans = {s["name"]: s for s in tracer.collect()}
    assert spans["root"]["parent_span_id"] is None
    assert spans["child"]["parent_span_id"] == spans["root"]["span_id"]
    assert spans["grandchild"]["parent_span_id"] == spans["child"]["span_id"]
    # All three share the same trace_id.
    assert spans["root"]["trace_id"] == spans["child"]["trace_id"] == spans["grandchild"]["trace_id"]
    # Spans are closed with a duration.
    assert spans["root"]["status"] == "closed"
    assert spans["root"]["duration_ms"] is not None
    assert spans["root"]["duration_ms"] >= 0


# =====================================================================
# Health monitor
# =====================================================================


def test_health_monitor_snapshot():
    """HealthMonitor.snapshot rolls up the worst reported state."""
    hm = HealthMonitor()
    hm.register("provider_router")
    hm.register("memory_store")
    hm.register("audit_log")

    hm.report("provider_router", HealthState.HEALTHY)
    hm.report("memory_store", HealthState.WARNING, "degraded performance")
    hm.report("audit_log", HealthState.HEALTHY)

    snap = hm.snapshot()
    assert snap["overall_state"] == "warning"
    assert snap["component_count"] == 3
    assert "provider_router" in snap["components"]
    assert snap["components"]["memory_store"]["state"] == "warning"
    assert "degraded performance" in (snap["components"]["memory_store"]["message"] or "")


def test_readiness_probe_blocks_on_degraded():
    """ReadinessProbe.ready() is False when any critical component is not ready."""
    hm = HealthMonitor()
    probe = ReadinessProbe(hm)
    probe.mark_critical("provider_router")
    probe.mark_critical("memory_store")

    hm.report("provider_router", HealthState.HEALTHY)
    hm.report("memory_store", HealthState.DEGRADED)

    assert probe.ready() is False
    reasons = probe.not_ready_reasons()
    assert any(r["component"] == "memory_store" for r in reasons)

    hm.report("memory_store", HealthState.HEALTHY)
    assert probe.ready() is True


# =====================================================================
# Secret redaction
# =====================================================================


def test_logger_redacts_secrets():
    """StructuredLogger NEVER emits secret values — known secret keys
    are replaced with '<redacted>'."""
    buf = io.StringIO()
    logger = StructuredLogger(name="test", format="json", stream=buf)
    logger.info(
        "tool.invoke",
        action="http.get",
        api_key="sk-supersecretvalue1234567890",
        password="hunter2",
        token="Bearer abcdefghijklmnopqrstuvwxyz123456",
        benign_field="kept",
        nested={
            "deep_secret": "v",
            "deep_safe": "ok",
        },
    )
    line = json.loads(buf.getvalue().strip())
    assert line["api_key"] == "<redacted>"
    assert line["password"] == "<redacted>"
    assert line["token"] == "<redacted>"
    assert line["benign_field"] == "kept"
    assert line["nested"]["deep_secret"] == "<redacted>"
    assert line["nested"]["deep_safe"] == "ok"
    # The actual secret strings must NEVER appear in the output.
    raw = buf.getvalue()
    assert "sk-supersecretvalue1234567890" not in raw
    assert "hunter2" not in raw


def test_redact_handles_nested_structures():
    """`redact` walks lists and dicts recursively."""
    out = redact({
        "list_of_dicts": [
            {"password": "p1", "name": "alice"},
            {"token": "tok", "name": "bob"},
        ],
        "deep": {"auth_credential": "c"},
    })
    assert out["list_of_dicts"][0]["password"] == "<redacted>"
    assert out["list_of_dicts"][0]["name"] == "alice"
    assert out["list_of_dicts"][1]["token"] == "<redacted>"
    assert out["deep"]["auth_credential"] == "<redacted>"
