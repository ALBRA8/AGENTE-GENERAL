"""Unit tests for the evidence subpackage."""

from __future__ import annotations

import asyncio

import pytest

from albra_seed.core.types import EvidenceType
from albra_seed.evidence import (
    CitationGate,
    EvidenceContract,
    EvidenceIntegrity,
    EvidenceManager,
    InMemoryEvidenceStore,
)


def _run(coro):
    return asyncio.run(coro)


def test_evidence_manager_registers_evidence():
    mgr = EvidenceManager(store=InMemoryEvidenceStore())
    ev = _run(
        mgr.register(
            source="tool:web_search",
            source_type="tool",
            content={"query": "x", "result": "y"},
            evidence_type=EvidenceType.FACT,
            confidence=0.95,
        )
    )
    assert ev.evidence_id is not None
    assert ev.integrity_hash is not None
    fetched = _run(mgr.retrieve(ev.evidence_id))
    assert fetched is not None
    assert fetched.source_type == "tool"
    assert fetched.evidence_type == EvidenceType.FACT


def test_fact_requires_high_confidence():
    contract = EvidenceContract()
    from albra_seed.core.types import Evidence, new_id

    # FACT with confidence < 0.9 must fail
    low_conf = Evidence(
        evidence_id=new_id("ev"),
        source="tool:x",
        source_type="tool",
        evidence_type=EvidenceType.FACT,
        confidence=0.5,
    )
    with pytest.raises(Exception):
        contract.validate(low_conf)

    # FACT with confidence >= 0.9 but bad source_type must fail
    bad_source = Evidence(
        evidence_id=new_id("ev"),
        source="agent",
        source_type="agent",  # not in {tool, external, verified_provider}
        evidence_type=EvidenceType.FACT,
        confidence=0.95,
    )
    with pytest.raises(Exception):
        contract.validate(bad_source)

    # FACT with confidence >= 0.9 AND tool source must pass
    good = Evidence(
        evidence_id=new_id("ev"),
        source="tool:y",
        source_type="tool",
        evidence_type=EvidenceType.FACT,
        confidence=0.95,
    )
    contract.validate(good)


def test_inference_never_promoted_to_fact():
    contract = EvidenceContract()
    from albra_seed.core.types import Evidence, new_id

    inf = Evidence(
        evidence_id=new_id("ev"),
        source="agent",
        source_type="verified_provider",
        evidence_type=EvidenceType.INFERENCE,
        confidence=0.95,  # meets threshold but is INFERENCE
    )
    # contract.validate does NOT auto-promote
    validated = contract.validate(inf)
    assert validated.evidence_type == EvidenceType.INFERENCE

    # explicit promote_to_fact only works if conditions met
    promoted = contract.promote_to_fact(inf)
    assert promoted.evidence_type == EvidenceType.FACT

    # UNKNOWN must never be promoted
    unk = Evidence(
        evidence_id=new_id("ev"),
        source="agent",
        source_type="agent",
        evidence_type=EvidenceType.UNKNOWN,
        confidence=0.99,
    )
    with pytest.raises(Exception):
        contract.promote_to_fact(unk)


def test_citation_gate_refuses_without_evidence():
    gate = CitationGate()
    # require evidence first
    gate.require_evidence("the sky is blue")
    # since we have no evidence, refuse
    gate.refuse("the sky is blue", reason="no supporting evidence")
    assert gate.refused("the sky is blue") is True
    assert gate.has_citation_for("the sky is blue") is False

    # cite evidence
    gate.cite("the sky is blue", "ev-1")
    assert gate.has_citation_for("the sky is blue") is True

    # mark unverified for a different claim
    gate.mark_unverified("the earth is flat", reason="unsupported")
    assert gate.unverified("the earth is flat") is True

    # request_evidence
    gate.request_evidence("query: latest measurements")
    assert gate.last() is not None
    assert gate.last().action == "request_evidence"


def test_evidence_integrity_detects_tampering():
    from albra_seed.core.types import Evidence, new_id

    ev = Evidence(
        evidence_id=new_id("ev"),
        source="tool:x",
        source_type="tool",
        evidence_type=EvidenceType.FACT,
        confidence=0.95,
        content="original",
    )
    EvidenceIntegrity.stamp(ev)
    assert EvidenceIntegrity.verify(ev) is True
    assert EvidenceIntegrity.detect_tampering(ev) is False

    # tamper with content
    ev.content = "tampered"
    assert EvidenceIntegrity.verify(ev) is False
    assert EvidenceIntegrity.detect_tampering(ev) is True

    with pytest.raises(ValueError):
        EvidenceIntegrity.assert_intact(ev)

    # re-stamp and verify pass
    EvidenceIntegrity.stamp(ev)
    assert EvidenceIntegrity.verify(ev) is True


def test_evidence_manager_promote_to_fact_explicit_only():
    mgr = EvidenceManager(store=InMemoryEvidenceStore())
    inf = _run(
        mgr.register_inference(
            source="provider:openai",
            source_type="verified_provider",
            content="hypothesis",
            confidence=0.93,
        )
    )
    assert inf.evidence_type == EvidenceType.INFERENCE
    promoted = _run(mgr.promote_to_fact(inf))
    assert promoted.evidence_type == EvidenceType.FACT
    assert promoted.integrity_hash is not None
