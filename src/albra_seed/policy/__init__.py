"""Policy subpackage — declarative, first-match-wins policy engine.

CRITICAL INVARIANTS:
  * DEFAULT-DENY. Unknown actions → DENY.
  * First-match-wins (priority desc, insertion order for ties).
  * ALLOW requires BOTH permission AND autonomy. Insufficient autonomy
    downgrades ALLOW → REQUIRE_HITL. Missing permission downgrades
    ALLOW → DENY.
  * NO DOMAIN POLICIES in the seed. Specialization layers register rules.
  * Autonomy ≠ Permission. High autonomy without permission = DENY.
"""

from albra_seed.policy.engine import PolicyEngine, PolicyVerdict
from albra_seed.policy.evaluator import (
    ActionContext,
    PolicyEvaluation,
    PolicyEvaluator,
)
from albra_seed.policy.rules import PolicyRule, PolicyRuleSet

__all__ = [
    "PolicyEngine",
    "PolicyVerdict",
    "PolicyRule",
    "PolicyRuleSet",
    "PolicyEvaluator",
    "PolicyEvaluation",
    "ActionContext",
]
