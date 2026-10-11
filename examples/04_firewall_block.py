#!/usr/bin/env python3
"""04_firewall_block.py — Try an action blocked by the firewall.

Demonstrates:
    1. ActionFirewall with NO rules → DEFAULT-DENY.
    2. Registering an ALLOW rule for one specific action.
    3. Verifying that ALLOW works for the allowed action.
    4. Verifying that unknown actions are still DENY (default-deny).
    5. Showing that ALLOW on one action does NOT open other actions.

Run:
    python examples/04_firewall_block.py
"""

from __future__ import annotations

import asyncio

from albra_seed import PolicyDecision, RiskLevel, AutonomyLevel
from albra_seed.core.types import RunContext
from albra_seed.firewall.firewall import ActionFirewall
from albra_seed.firewall.rules import FirewallRule


async def main() -> None:
    # 1. Fresh firewall — no rules → default-deny for everything.
    fw = ActionFirewall()

    v = await fw.check("any_action_at_all")
    print(f"unknown action   : decision={v.decision.value} allowed={v.allowed} "
          f"reason={v.reason}")
    assert v.decision == PolicyDecision.DENY
    assert v.reason == "no_matching_rule"

    # 2. Register an ALLOW rule for tool.echo.execute only.
    fw.add_rule(FirewallRule(
        name="allow_echo",
        action_pattern="tool.echo.execute",
        actor_pattern="*",
        risk_threshold=RiskLevel.UNKNOWN,   # match any risk (UNKNOWN rank -1)
        autonomy_required=AutonomyLevel.L0_OBSERVE,
        decision=PolicyDecision.ALLOW,
        priority=10,
    ))

    ctx = RunContext(agent_id="probe", task="demo",
                     autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
                     risk_level=RiskLevel.LOW)

    allowed = await fw.check("tool.echo.execute", context=ctx)
    print(f"echo action      : decision={allowed.decision.value} "
          f"allowed={allowed.allowed} matched_rule={allowed.matched_rule_id}")

    # 3. Another action is STILL denied — explicit allow is per-action.
    blocked = await fw.check("tool.delete_database.execute", context=ctx)
    print(f"delete action    : decision={blocked.decision.value} "
          f"allowed={blocked.allowed} reason={blocked.reason}")


if __name__ == "__main__":
    asyncio.run(main())
