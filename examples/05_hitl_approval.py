#!/usr/bin/env python3
"""05_hitl_approval.py — High-risk action requiring approval; approve it.

Demonstrates the HITL ApprovalEngine:
    1. Create an ApprovalRequest for a high-risk action.
    2. Verify the request is PENDING.
    3. Approve the request as a human approver.
    4. Verify the request is now APPROVED (terminal).
    5. Verify the tamper-evident evidence chain is intact.

Run:
    python examples/05_hitl_approval.py
"""

from __future__ import annotations

import asyncio

from albra_seed import ApprovalStatus
from albra_seed.approvals.engine import ApprovalEngine


async def main() -> None:
    engine = ApprovalEngine()

    # 1. A high-risk action requires HITL approval.
    approval = await engine.request(
        action="tool.high_risk.execute",
        actor="agent:demo-agent",
        scope="single",
    )
    print(f"requested        : approval_id={approval.approval_id}")
    print(f"                 : status={approval.status.value}")

    # 2. Confirm it's PENDING and visible in list_pending().
    pending = engine.list_pending()
    print(f"pending queue    : {len(pending)} request(s)")

    # 3. Approve as a human approver.
    decision = await engine.approve(approval.approval_id, approver="human-ops")
    print(f"approved         : status={decision.status.value} "
          f"decided_by={decision.decided_by}")

    # 4. Verify the request is now APPROVED (terminal state).
    req = engine.get(approval.approval_id)
    assert req is not None
    print(f"final state      : {req.status.value} (terminal={req.is_terminal})")

    # 5. The tamper-evident evidence chain must still verify.
    ok = engine.verify_evidence_chain()
    print(f"evidence chain   : intact={ok}")

    # 6. Trying to approve again must FAIL (terminal state).
    again = await engine.approve(approval.approval_id, approver="human-ops")
    print(f"re-approve       : status={again.status.value} reason={again.reason}")


if __name__ == "__main__":
    asyncio.run(main())
