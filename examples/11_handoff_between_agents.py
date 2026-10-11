#!/usr/bin/env python3
"""11_handoff_between_agents.py — Agent A hands off to Agent B.

Demonstrates agent-to-agent handoff:
    1. Register a target agent (Agent B) with the AgentRegistry.
    2. Build a HandoffRequest from Agent A → Agent B.
    3. HandoffManager.initiate() routes the request to B's handler.
    4. Print the HandoffResponse (status, result, confidence).
    5. Verify the evidence chain recorded the handoff.

Run:
    python examples/11_handoff_between_agents.py
"""

from __future__ import annotations

import asyncio

from albra_seed import RiskLevel
from albra_seed.handoff.contract import HandoffRequest, HandoffResponse, HandoffStatus
from albra_seed.handoff.manager import HandoffManager
from albra_seed.handoff.registry import AgentRegistry, AgentRecord


async def main() -> None:
    # 1. Register Agent B's handler.
    registry = AgentRegistry()

    async def agent_b_handler(req: HandoffRequest) -> HandoffResponse:
        # Agent B does its work and returns a success response.
        return HandoffResponse.success(
            execution_id=req.execution_id,
            result={"summary": "handled by agent B", "steps": 3},
            confidence=0.92,
            responder="agent-B",
        )

    registry.register(AgentRecord(
        agent_id="agent-B",
        name="agent-B",
        description="Demo target agent for handoff.",
        handler=agent_b_handler,
        metadata={"specialization": "demo"},
    ))

    # 2. Build the handoff manager + request.
    mgr = HandoffManager(registry=registry)
    req = HandoffRequest(
        execution_id="exec-001",
        requester="agent-A",
        target="agent-B",
        objective="summarize the dataset",
        input={"dataset_ref": "s3://bucket/key"},
        requested_output={"summary": "string"},
        risk=RiskLevel.MEDIUM,
    )

    # 3. Initiate handoff.
    resp = await mgr.initiate(req)
    print(f"status            : {resp.status}")
    print(f"responder         : {resp.responder}")
    print(f"result            : {resp.result}")
    print(f"confidence        : {resp.confidence}")
    print(f"errors            : {resp.errors}")

    # 4. Verify the evidence chain.
    print(f"evidence chain len: {len(mgr.evidence_chain)}")
    assert resp.status == HandoffStatus.SUCCEEDED


if __name__ == "__main__":
    asyncio.run(main())
