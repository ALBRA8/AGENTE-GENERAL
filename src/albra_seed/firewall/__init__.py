"""Firewall subpackage — DEFAULT-DENY action gate.

CRITICAL INVARIANTS enforced here:
  * Every external action MUST pass through `ActionFirewall.evaluate`.
  * DEFAULT-DENY: no matching rule → DENY.
  * KillSwitch is GLOBAL and blocks ALL external actions when active.
  * Cooldowns + rate limits are enforced BEFORE rule evaluation.
  * Kill switch activation/deactivation is audit-logged.
"""

from albra_seed.firewall.cooldown import CooldownManager, CooldownRecord
from albra_seed.firewall.firewall import ActionFirewall, FirewallVerdict
from albra_seed.firewall.kill_switch import KillSwitch, KillSwitchState
from albra_seed.firewall.rate_limit import RateLimiter
from albra_seed.firewall.rules import FirewallRule

__all__ = [
    "ActionFirewall",
    "FirewallVerdict",
    "FirewallRule",
    "KillSwitch",
    "KillSwitchState",
    "CooldownManager",
    "CooldownRecord",
    "RateLimiter",
]
