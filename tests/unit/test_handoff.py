"""Unit tests for the handoff subpackage."""

import asyncio
from datetime import timedelta

import pytest

from albra_seed.core.types import RiskLevel, utc_now
from albra_seed.handoff import (
    AgentRecord,
    AgentRegistry,
    HandoffEvidence,
    HandoffEvidenceChain,
    HandoffManager,
    HandoffRequest,
    HandoffResponse,
    HandoffStatus,
    HandoffTimeout,
    build_evidence,
)


# -------------------------------------------------------------------
# 1. Handoff request + response shape
# -------------------------------------------------------------------


def test_handoff_request_and_response():
    req = HandoffRequest(
        execution_id="exec-1",
        requester="agent-A",
        target="agent-B",
        objective="summarise input",
        input={"text": "hello"},
        risk=RiskLevel.LOW,
    )
    assert req.execution_id == "exec-1"
    assert req.requester == "agent-A"
    assert req.target == "agent-B"
    assert req.deadline is None
    assert req.is_expired() is False

    req.with_timeout(10.0)
    assert req.deadline is not None
    assert req.deadline_in_seconds is not None
    assert 0 < req.deadline_in_seconds <= 10.0

    resp = HandoffResponse.success(
        "exec-1",
        result={"summary": "hi"},
        confidence=0.8,
        responder="agent-B",
    )
    assert resp.status == HandoffStatus.SUCCEEDED
    assert resp.is_terminal is True
    assert resp.confidence == 0.8

    fail = HandoffResponse.failure("exec-1", errors=["bad input"])
    assert fail.status == HandoffStatus.FAILED
    assert fail.is_terminal is True


# -------------------------------------------------------------------
# 2. HandoffManager initiates and routes via AgentRegistry
# -------------------------------------------------------------------


def test_handoff_manager_initiates():
    registry = AgentRegistry()

    async def handler(req: HandoffRequest) -> HandoffResponse:
        return HandoffResponse.success(
            req.execution_id,
            result={"doubled": req.input.get("value", 0) * 2},
            confidence=0.9,
            responder=req.target,
        )

    registry.register(
        AgentRecord(agent_id="agent-B", name="agent-b", handler=handler)
    )

    mgr = HandoffManager(registry=registry)
    req = HandoffRequest(
        execution_id="exec-2",
        requester="agent-A",
        target="agent-B",
        objective="double the value",
        input={"value": 21},
        risk=RiskLevel.LOW,
    )
    resp = asyncio.run(mgr.initiate(req))
    assert resp.status == HandoffStatus.SUCCEEDED
    assert resp.result == {"doubled": 42}
    assert resp.confidence == 0.9
    # Evidence was appended to the chain.
    assert len(mgr.evidence_chain) == 1
    assert mgr.evidence_chain.verify_chain() is True


def test_handoff_manager_rejects_unknown_target():
    mgr = HandoffManager()
    req = HandoffRequest(
        execution_id="exec-3",
        requester="agent-A",
        target="agent-unknown",
        objective="do thing",
        input={},
    )
    resp = asyncio.run(mgr.initiate(req))
    assert resp.status == HandoffStatus.REJECTED
    assert resp.errors and "not available" in resp.errors[0]


# -------------------------------------------------------------------
# 3. HandoffTimeout fires when deadline is exceeded
# -------------------------------------------------------------------


def test_handoff_timeout_when_deadline_exceeded():
    req = HandoffRequest(
        execution_id="exec-4",
        requester="agent-A",
        target="agent-B",
        objective="too slow",
        input={},
    )
    # Set deadline in the past.
    req.deadline = utc_now() - timedelta(seconds=1)
    timeout = HandoffTimeout(req)

    assert timeout.is_expired() is True
    assert timeout.remaining_seconds() == 0.0

    resp = timeout.fire("deadline exceeded")
    assert resp.status == HandoffStatus.TIMEOUT
    assert timeout.fired is True
    assert timeout.audit_record is not None
    assert timeout.audit_record.message == "deadline exceeded"


def test_handoff_manager_initiate_expired_rejects():
    registry = AgentRegistry()

    async def slow_handler(req: HandoffRequest) -> HandoffResponse:
        await asyncio.sleep(0.01)
        return HandoffResponse.success(req.execution_id, result={})

    registry.register(
        AgentRecord(agent_id="agent-B", name="b", handler=slow_handler)
    )
    mgr = HandoffManager(registry=registry)
    req = HandoffRequest(
        execution_id="exec-5",
        requester="agent-A",
        target="agent-B",
        objective="expired",
        input={},
    )
    req.deadline = utc_now() - timedelta(seconds=1)
    resp = asyncio.run(mgr.initiate(req))
    assert resp.status == HandoffStatus.TIMEOUT
    # Evidence chain still records the timeout outcome.
    assert len(mgr.evidence_chain) == 1


# -------------------------------------------------------------------
# 4. HandoffEvidence is tamper-evident
# -------------------------------------------------------------------


def test_handoff_evidence_tamper_evident():
    req = HandoffRequest(
        execution_id="exec-6",
        requester="agent-A",
        target="agent-B",
        objective="verify",
        input={"x": 1},
    )
    resp = HandoffResponse.success(
        req.execution_id, result={"y": 2}, confidence=0.7, responder="agent-B"
    )

    entry = build_evidence(req, resp)
    assert isinstance(entry, HandoffEvidence)
    assert entry.verify() is True

    # Tamper: mutate a field AFTER sealing -> hash no longer matches.
    entry.response_snapshot = {"tampered": True}
    assert entry.verify() is False


def test_handoff_evidence_chain_detects_tamper():
    chain = HandoffEvidenceChain()

    req1 = HandoffRequest(
        execution_id="exec-7",
        requester="A",
        target="B",
        objective="o1",
        input={"i": 1},
    )
    resp1 = HandoffResponse.success(req1.execution_id, result={"o": 1})
    chain.append(evidence_id="ev-1", request=req1, response=resp1)

    req2 = HandoffRequest(
        execution_id="exec-8",
        requester="A",
        target="B",
        objective="o2",
        input={"i": 2},
    )
    resp2 = HandoffResponse.success(req2.execution_id, result={"o": 2})
    chain.append(evidence_id="ev-2", request=req2, response=resp2)

    assert len(chain) == 2
    assert chain.verify_chain() is True

    # Tamper with the first entry's response snapshot.
    chain.entries()[0].response_snapshot = {"hacked": True}
    assert chain.verify_chain() is False


def test_handoff_manager_receive_dispatches_handler():
    mgr = HandoffManager()

    async def receive_handler(req: HandoffRequest) -> HandoffResponse:
        return HandoffResponse.success(
            req.execution_id,
            result={"echo": req.input.get("payload")},
        )

    req = HandoffRequest(
        execution_id="exec-9",
        requester="agent-A",
        target="agent-B",
        objective="receive",
        input={"payload": "hello"},
    )
    resp = asyncio.run(mgr.receive(req, receive_handler))
    assert resp.status == HandoffStatus.SUCCEEDED
    assert resp.result == {"echo": "hello"}
