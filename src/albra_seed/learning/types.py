"""Universal Learning types.

The SEED's *Universal Learning* mechanism is a **proposal pipeline with
HITL** — NOT an ML trainer. It turns observations (feedback, decision
outcomes, execution traces) into `LearningProposal` records, walks each
one through a `PROPOSED → REVIEW_REQUIRED → APPROVED → APPLIED` lifecycle
(or `REJECTED` / `SUPERSEDED`), and stops there. The system NEVER
auto-modifies code or skills; `apply()` only marks the proposal as applied
so an external human/dev process can confirm the change was made.

The two enums (`LearningSignal`, `ProposalStatus`) are deliberately
domain-agnostic. `target_component` is one of:
    "skill" | "memory" | "policy" | "tool" | "prompt" | "other"

This subpackage is a SIBLING of `feedback`, `memory.decisions`, `audit`
and `evidence`:
    * evidence  = "what we know"      (facts)
    * audit      = "what happened"     (events)
    * feedback   = "how good the outcome was"  (evaluation)
    * decision   = "what we chose and why"      (choice)
    * learning   = "what we should change next time"  (improvement)

Learning records may reference feedback_ids, decision_ids and
execution_ids, but never replace them. ID prefix is `learn_`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now


class LearningSignal(str, Enum):
    """Origin of the learning observation.

    Four domain-agnostic values:
      * FEEDBACK    — derived from FeedbackManager records.
      * OUTCOME     — derived from a DecisionRecord outcome mismatch.
      * DECISION    — derived from a decision pattern.
      * EXPERIENCE  — derived from an ExecutionTrace (errors / warnings).

    The four are independent of the ProposalStatus lifecycle — a single
    signal type can produce proposals of any status.
    """

    FEEDBACK = "feedback"
    OUTCOME = "outcome"
    DECISION = "decision"
    EXPERIENCE = "experience"


class ProposalStatus(str, Enum):
    """Lifecycle of a LearningProposal.

    Canonical transitions enforced by LearningManager:

        PROPOSED ─┬─> REVIEW_REQUIRED ─> APPROVED ─> APPLIED
                  ├─> REJECTED
                  └─> SUPERSEDED (by a newer proposal)

    CRITICAL: PROPOSED → APPLIED directly is FORBIDDEN. `apply()` only
    succeeds when status == APPROVED — this is the HITL gate.
    `apply()` does NOT modify any code; it only records that an external
    change has been applied. The actual change happens externally.
    """

    PROPOSED = "proposed"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    SUPERSEDED = "superseded"


# Canonical target_component values — kept as a set for validation use.
LEARNING_TARGET_COMPONENTS: frozenset[str] = frozenset(
    {"skill", "memory", "policy", "tool", "prompt", "other"}
)


class LearningProposal(BaseModel):
    """A single learning proposal — immutable once written.

    Universal fields:
      * proposal_id            — prefixed `learn_` (never confused with
                                  `fb_` / `ev_` / `aud_` / `dec_` ids).
      * agent_id               — the agent the proposal applies to.
      * signal_type            — LearningSignal origin.
      * source_feedback_ids    — feedback records that triggered it.
      * source_decision_ids    — decisions referenced.
      * source_execution_ids   — executions referenced.
      * observation            — what was observed (free-form text).
      * proposed_change        — description of the proposed improvement.
      * rationale              — why this change is recommended.
      * target_component       — one of LEARNING_TARGET_COMPONENTS.
      * target_id              — optional concrete target (e.g. skill_id).
      * confidence             — 0.0..1.0; default 0.0.
      * evidence_ids           — supporting evidence (optional).
      * execution_id           — propagated via CorrelationTracker.
      * correlation_id         — propagated via CorrelationTracker.
      * status                  — ProposalStatus; default PROPOSED.
      * created_at / reviewed_at / reviewed_by / applied_at — lifecycle.
      * rejection_reason       — set when status=REJECTED.
      * metadata               — domain-specific extensions go here.
    """

    proposal_id: str = Field(default_factory=lambda: new_id("learn"))
    agent_id: str
    signal_type: LearningSignal
    source_feedback_ids: list[str] = Field(default_factory=list)
    source_decision_ids: list[str] = Field(default_factory=list)
    source_execution_ids: list[str] = Field(default_factory=list)
    observation: str
    proposed_change: str
    rationale: str
    target_component: str = "other"
    target_id: str | None = None
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    execution_id: str | None = None
    correlation_id: str | None = None
    status: ProposalStatus = ProposalStatus.PROPOSED
    created_at: datetime = Field(default_factory=utc_now)
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    applied_at: datetime | None = None
    rejection_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "LearningSignal",
    "ProposalStatus",
    "LearningProposal",
    "LEARNING_TARGET_COMPONENTS",
]
