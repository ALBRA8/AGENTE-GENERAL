"""Universal Learning subpackage.

Public API:
    from albra_seed.learning import (
        LearningManager,
        LearningError,
        LearningStore,
        InMemoryLearningStore,
        SQLiteLearningStore,
        ProposalGenerator,
        LearningProposal,
        ProposalStatus,
        LearningSignal,
    )

The learning mechanism is the SEED's *improvement pipeline*: it turns
observations (feedback, decision outcomes, execution traces) into
`LearningProposal` records and walks each one through a HITL lifecycle:

    PROPOSED → REVIEW_REQUIRED → APPROVED → APPLIED
                                       ↘ REJECTED
                                       ↘ SUPERSEDED

CRITICAL: this is NOT an ML trainer. `apply()` does NOT auto-modify
code or skills — it only marks the proposal as applied so an external
human/dev process can confirm the change was made. The HITL gate is
enforced: PROPOSED → APPLIED directly is FORBIDDEN.

This subpackage is a SIBLING of `feedback`, `memory.decisions`, `audit`
and `evidence`:
    * evidence   = "what we know"             (facts)
    * audit       = "what happened"            (events)
    * feedback    = "how good the outcome was"  (evaluation)
    * decision    = "what we chose and why"     (choice)
    * learning    = "what we should change next time"  (improvement)

Learning records may reference feedback_ids, decision_ids and
execution_ids, but never replace them. ID prefix is `learn_`.
"""

from albra_seed.learning.manager import LearningError, LearningManager
from albra_seed.learning.proposal import ProposalGenerator
from albra_seed.learning.store import (
    InMemoryLearningStore,
    LearningStore,
    SQLiteLearningStore,
)
from albra_seed.learning.types import (
    LEARNING_TARGET_COMPONENTS,
    LearningProposal,
    LearningSignal,
    ProposalStatus,
)

__all__ = [
    "LearningManager",
    "LearningError",
    "LearningStore",
    "InMemoryLearningStore",
    "SQLiteLearningStore",
    "ProposalGenerator",
    "LearningProposal",
    "ProposalStatus",
    "LearningSignal",
    "LEARNING_TARGET_COMPONENTS",
]
