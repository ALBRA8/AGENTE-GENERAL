#!/usr/bin/env python3
"""08_kill_switch.py — Activate the kill switch, verify all actions are blocked.

Demonstrates the global KillSwitch:
    1. Build an ActionFirewall with an explicit ALLOW rule.
    2. Verify the ALLOW rule works (action allowed) before activation.
    3. Activate the kill switch (async; audit-logged).
    4. Verify the SAME action is now DENY (kill switch overrides ALLOW).
    5. Deactivate the kill switch; verify a post-deactivation cooldown is in effect.

Run:
    python examples/08_kill_switch.py
"""

from __future__ import annotations

import asyncio

from albra_seed import AutonomyLevel, PolicyDecision, RiskLevel
from albra_seed.core.types import RunContext
from albra_seed.firewall.firewall import ActionFirewall
from albra_seed.firewall.rules import FirewallRule


async def main() -> None:
    fw = ActionFirewall()
    fw.add_rule(FirewallRule(
        name="allow_echo",
        action_pattern="tool.echo.execute",
        risk_threshold=RiskLevel.UNKNOWN,
        autonomy_required=AutonomyLevel.L0_OBSERVE,
        decision=PolicyDecision.ALLOW,
        priority=10,
    ))

    ctx = RunContext(agent_id="demo", task="kill-switch",
                     autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
                     risk_level=RiskLevel.LOW)

    # 1. Before activation: ALLOW rule fires.
    before = await fw.check("tool.echo.execute", context=ctx)
    print(f"before kill sw   : decision={before.decision.value} allowed={before.allowed}")

    # 2. Activate the global kill switch.
    await fw.kill_switch.activate(
        reason="demonstration: blocking all external actions",
        activated_by="security-demo",
    )
    print(f"kill switch      : active={fw.kill_switch.is_active()}")

    # 3. SAME action is now DENY — kill switch overrides the explicit ALLOW.
    after = await fw.check("tool.echo.execute", context=ctx)
    print(f"after kill sw    : decision={after.decision.value} "
          f"allowed={after.allowed} kill_switch_active={after.kill_switch_active}")

    # 4. Even an unknown action is still DENY (no double-allow leakage).
    other = await fw.check("totally_unknown_action", context=ctx)
    print(f"unknown action   : decision={other.decision.value} "
          f"reason={other.reason}")

    # 5. Deactivate; verify cooldown enforced.
    await fw.kill_switch.deactivate(by="security-demo")
    print(f"after deactivate : active={fw.kill_switch.is_active()} "
          f"in_cooldown={fw.kill_switch.in_cooldown()}")


if __name__ == "__main__":
    asyncio.run(main())
