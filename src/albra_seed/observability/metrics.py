"""MetricsRegistry — counters, gauges, histograms.

Three metric kinds, all tag-aware:
  * Counter  — monotonically increasing (e.g. `requests_total`).
  * Gauge    — current value, can go up or down (e.g. `queue_depth`).
  * Histogram — observed value into a fixed set of buckets
                 (e.g. `request_duration_seconds`).

`snapshot()` returns a serializable dict view of every metric, useful for
exposing to a Prometheus-style scrape endpoint or for diagnostic dumps.

The registry is intentionally thread-safe via a simple lock — high-
throughput production deployments should swap in a lock-free impl, but
this seed prefers correctness.
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from typing import Any

_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)


def _tag_key(tags: dict[str, Any] | None) -> str:
    if not tags:
        return ""
    return "|".join(f"{k}={tags[k]}" for k in sorted(tags.keys()))


class _Counter:
    __slots__ = ("name", "values")

    def __init__(self, name: str) -> None:
        self.name = name
        self.values: dict[str, float] = defaultdict(float)

    def inc(self, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        if value < 0:
            raise ValueError(f"counter '{self.name}' cannot decrease (got {value})")
        self.values[_tag_key(tags)] += float(value)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {"name": self.name, "type": "counter", "tags": _parse_tags(k), "value": v}
            for k, v in sorted(self.values.items())
        ]


class _Gauge:
    __slots__ = ("name", "values")

    def __init__(self, name: str) -> None:
        self.name = name
        self.values: dict[str, float] = defaultdict(float)

    def set(self, value: float, tags: dict[str, Any] | None = None) -> None:
        self.values[_tag_key(tags)] = float(value)

    def inc(self, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        self.values[_tag_key(tags)] += float(value)

    def dec(self, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        self.values[_tag_key(tags)] -= float(value)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {"name": self.name, "type": "gauge", "tags": _parse_tags(k), "value": v}
            for k, v in sorted(self.values.items())
        ]


class _Histogram:
    __slots__ = ("name", "buckets", "_tagged")

    def __init__(self, name: str, buckets: tuple[float, ...] = _DEFAULT_BUCKETS) -> None:
        self.name = name
        self.buckets = tuple(sorted(buckets))
        # Track per-tag-set counts in a nested structure for snapshot.
        # default_factory must be a regular function (not lambda) to keep
        # the closure over `self.buckets` explicit.
        def _new_slot() -> dict[str, Any]:
            return {
                "buckets": {b: 0 for b in self.buckets},
                "sum": 0.0,
                "count": 0,
                "min": math.inf,
                "max": -math.inf,
            }
        self._tagged: dict[str, dict[str, Any]] = defaultdict(_new_slot)

    def observe(self, value: float, tags: dict[str, Any] | None = None) -> None:
        v = float(value)
        key = _tag_key(tags)
        slot = self._tagged[key]
        slot["sum"] += v
        slot["count"] += 1
        if v < slot["min"]:
            slot["min"] = v
        if v > slot["max"]:
            slot["max"] = v
        for b in self.buckets:
            if v <= b:
                slot["buckets"][b] += 1

    def snapshot(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for k, slot in sorted(self._tagged.items()):
            out.append({
                "name": self.name,
                "type": "histogram",
                "tags": _parse_tags(k),
                "count": slot["count"],
                "sum": slot["sum"],
                "min": slot["min"] if slot["count"] > 0 else 0.0,
                "max": slot["max"] if slot["count"] > 0 else 0.0,
                "buckets": dict(slot["buckets"]),
            })
        return out


def _parse_tags(key: str) -> dict[str, str]:
    if not key:
        return {}
    out: dict[str, str] = {}
    for part in key.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


class MetricsRegistry:
    """Thread-safe registry of counters / gauges / histograms.

    Methods auto-create the metric on first use — no separate
    `register()` call needed. `snapshot()` returns a flat list of metric
    points (one per tag-set) suitable for JSON serialization.
    """

    def __init__(self) -> None:
        self._counters: dict[str, _Counter] = {}
        self._gauges: dict[str, _Gauge] = {}
        self._histograms: dict[str, _Histogram] = {}
        self._lock = threading.Lock()

    def inc(self, name: str, value: float = 1.0, tags: dict[str, Any] | None = None) -> None:
        with self._lock:
            c = self._counters.get(name)
            if c is None:
                c = _Counter(name)
                self._counters[name] = c
            c.inc(value, tags)

    def set(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        with self._lock:
            g = self._gauges.get(name)
            if g is None:
                g = _Gauge(name)
                self._gauges[name] = g
            g.set(value, tags)

    def observe(self, name: str, value: float, tags: dict[str, Any] | None = None) -> None:
        with self._lock:
            h = self._histograms.get(name)
            if h is None:
                h = _Histogram(name)
                self._histograms[name] = h
        h.observe(value, tags)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {
                "counters": [],
                "gauges": [],
                "histograms": [],
            }
            for c in self._counters.values():
                out["counters"].extend(c.snapshot())
            for g in self._gauges.values():
                out["gauges"].extend(g.snapshot())
            for h in self._histograms.values():
                out["histograms"].extend(h.snapshot())
            return out

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


__all__ = ["MetricsRegistry"]
