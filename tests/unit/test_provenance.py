"""Unit tests for the provenance subpackage."""

from __future__ import annotations

import asyncio

import pytest

from albra_seed.provenance import (
    InMemoryProvenanceStore,
    ProvenanceChain,
    ProvenanceTracker,
    SQLiteProvenanceStore,
)


def _run(coro):
    return asyncio.run(coro)


def test_provenance_tracker_records_chain():
    tracker = ProvenanceTracker(store=InMemoryProvenanceStore())
    p1 = _run(
        tracker.track_input(
            source="user", record_ref="in-1", correlation_id="corr-1"
        )
    )
    p2 = _run(
        tracker.track_tool_call(
            tool_name="search",
            record_ref="tc-1",
            parent_provenance_id=p1.provenance_id,
            correlation_id="corr-1",
            execution_id="exec-1",
        )
    )
    p3 = _run(
        tracker.track_evidence(
            source="tool:search",
            source_type="tool",
            record_ref="ev-1",
            parent_provenance_id=p2.provenance_id,
            correlation_id="corr-1",
        )
    )
    p4 = _run(
        tracker.track_output(
            source="agent",
            record_ref="out-1",
            parent_provenance_id=p3.provenance_id,
            correlation_id="corr-1",
        )
    )

    # chain by record_kind/ref
    chain = ProvenanceChain(tracker.store)
    out_chain = _run(chain.trace("out-1", record_kind="output"))
    assert len(out_chain) >= 1
    ids = [p.provenance_id for p in out_chain]
    # walking back via parent should reach p1
    assert p4.provenance_id in ids
    assert p3.provenance_id in ids
    assert p2.provenance_id in ids
    assert p1.provenance_id in ids


def test_provenance_chain_trace():
    tracker = ProvenanceTracker(store=SQLiteProvenanceStore(":memory:"))
    root = _run(tracker.track_input(source="user", record_ref="in-1"))
    mid = _run(
        tracker.track_decision(
            decision="decided to call tool",
            record_ref="d-1",
            parent_provenance_id=root.provenance_id,
        )
    )
    leaf = _run(
        tracker.track_tool_call(
            tool_name="search",
            record_ref="tc-1",
            parent_provenance_id=mid.provenance_id,
            evidence_ids=["ev-1"],
        )
    )

    chain = ProvenanceChain(tracker.store)
    out = _run(chain.trace_by_provenance_id(leaf.provenance_id))
    # 3 nodes
    assert len(out) == 3
    # ordered: leaf first, root last
    assert out[0].provenance_id == leaf.provenance_id
    assert out[-1].provenance_id == root.provenance_id

    # evidence collected across the chain
    evs = _run(chain.evidence_ids_in_chain(out))
    assert "ev-1" in evs
    assert _run(chain.has_evidence("tc-1", record_kind="tool_call")) is True


def test_provenance_propagates_correlation_id():
    tracker = ProvenanceTracker(store=InMemoryProvenanceStore())
    corr = "corr-xyz"
    _run(tracker.track_input(source="user", record_ref="i-1", correlation_id=corr))
    _run(tracker.track_output(source="agent", record_ref="o-1", correlation_id=corr))
    _run(
        tracker.track_tool_call(
            tool_name="search",
            record_ref="t-1",
            correlation_id=corr,
            execution_id="exec-1",
        )
    )
    _run(tracker.track_decision(decision="d", record_ref="d-1", correlation_id="other"))

    results = _run(tracker.for_correlation(corr))
    assert len(results) == 3
    assert all(p.correlation_id == corr for p in results)

    # also verify execution_id lookup works
    exec_results = _run(tracker.for_execution("exec-1"))
    assert len(exec_results) == 1
    assert exec_results[0].correlation_id == corr
