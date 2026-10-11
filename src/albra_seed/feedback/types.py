"""Universal Feedback types.

Feedback is the SEED's universal abstraction for *outcome evaluation*:
any subsystem (human, automated evaluator, system monitor, another agent)
can record that an observed outcome matched / did not match an expected
one, optionally linked to a DecisionRecord and supported by Evidence.

The FeedbackType enum is deliberately DOMAIN-AGNOSTIC. It captures only
the *kind* of feedback (positive / negative / correction / human /
system / automated-evaluation). Any domain-specific classification lives
in `metadata`.

This subpackage is a sibling of `evidence` and `audit`. It is NOT a
re-implementation of either:
  * `Evidence`    = "what we know" (the facts).
  * `AuditEntry`  = "what happened" (the events).
  * `Feedback`    = "how good the outcome was" (the evaluation).

The three are complementary; feedback records may *reference* evidence
ids and audit entries, but never replace them.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now


class FeedbackType(str, Enum):
    """Domain-agnostic feedback categories.

    Six values, deliberately orthogonal:
      * POSITIVE              — outcome matched or exceeded expectation.
      * NEGATIVE              — outcome failed expectation.
      * CORRECTION            — a corrective signal (often from a human
                                or supervisor); implies the prior
                                outcome should be revised.
      * HUMAN_FEEDBACK        — feedback authored by a human.
      * SYSTEM_FEEDBACK        — feedback produced by the SEED itself
                                (e.g. runtime guard, resource monitor).
      * AUTOMATED_EVALUATION  — feedback produced by an automated
                                evaluator (e.g. scoring rubric, test
                                harness, LLM-as-judge).

    HUMAN_FEEDBACK / SYSTEM_FEEDBACK / AUTOMATED_EVALUATION describe the
    *origin* of the feedback; POSITIVE / NEGATIVE / CORRECTION describe
    its *polarity*. The two axes are independent — a human can give a
    positive or a negative feedback; an automated evaluator can issue a
    correction. Callers pick the value that best fits.
    """

    POSITIVE = "positive"
    NEGATIVE = "negative"
    CORRECTION = "correction"
    HUMAN_FEEDBACK = "human_feedback"
    SYSTEM_FEEDBACK = "system_feedback"
    AUTOMATED_EVALUATION = "automated_evaluation"


class FeedbackRecord(BaseModel):
    """A single feedback entry — immutable once written.

    Universal fields:
      * feedback_id      — prefixed `fb_` (never confused with `ev_`
                            evidence ids or `aud_` audit entry ids).
      * agent_id         — the agent the feedback applies to.
      * task_id          — optional task link.
      * context_id      — optional context link.
      * expected_outcome — what the caller expected (free-form Any).
      * actual_outcome   — what actually happened (free-form Any).
      * evaluation       — free-form text describing the evaluation.
      * feedback_type    — one of FeedbackType.
      * source           — who/what generated the feedback.
      * source_type      — human | system | automated | agent | external.
      * confidence       — 0.0..1.0; default 0.0.
      * evidence_ids     — supporting evidence (optional).
      * provenance_id    — link to a Provenance record (optional).
      * decision_id      — link to a DecisionRecord (optional).
      * skill_id         — link to a Skill (optional).
      * correlation_id   — propagated via CorrelationTracker.
      * execution_id     — propagated via CorrelationTracker.
      * timestamp        — utc_now().
      * metadata         — domain-specific extensions go here.
    """

    feedback_id: str = Field(default_factory=lambda: new_id("fb"))
    execution_id: str | None = None
    correlation_id: str | None = None
    agent_id: str
    task_id: str | None = None
    context_id: str | None = None
    expected_outcome: Any = None
    actual_outcome: Any = None
    evaluation: str
    feedback_type: FeedbackType
    source: str
    source_type: str
    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_id: str | None = None
    decision_id: str | None = None
    skill_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["FeedbackType", "FeedbackRecord"]
