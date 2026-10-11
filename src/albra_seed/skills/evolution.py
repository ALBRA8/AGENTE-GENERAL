"""Skill Evolution — metrics aggregation + improvement proposals.

ADDITIVE: extends the existing skills system. Does NOT replace
SkillRegistry or SkillLifecycle.

  * SkillMetrics           — aggregated execution/feedback stats per skill.
  * ImprovementProposal    — a structured proposal to evolve a skill.
  * SkillEvolutionManager  — collects metrics + walks proposals through
                             PROPOSED → REVIEW_REQUIRED → APPROVED → APPLIED.

CRITICAL (mirrors GAP-2 LearningManager):
  * apply() MUST NOT modify skill *code*. It only marks the proposal
    APPLIED; if `proposed_version` is set, it delegates to the existing
    SkillRegistry.upgrade_version() (bumps contract version + records a
    SkillLifecycle event). Handler code is left untouched.
  * PROPOSED → APPLIED is FORBIDDEN. apply() only succeeds when the
    proposal is APPROVED — this is the HITL gate.
  * ID prefix `skillprop_` is distinct from `fb_`/`ev_`/`aud_`/`dec_`/
    `learn_`/`prov_` so the proposal store can never be confused with
    the other stores.

Sibling of GAP-2 LearningManager. An ImprovementProposal may carry a
`source_learning_proposal_id` linking back to a LearningProposal whose
target_component="skill" — the two pipelines compose without coupling.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now

if TYPE_CHECKING:  # pragma: no cover - typing only
    from albra_seed.skills.contract import SkillContract
    from albra_seed.skills.registry import SkillRegistry


class SkillEvolutionError(RuntimeError):
    """Raised when a skill evolution lifecycle transition is invalid."""


class SkillMetrics(BaseModel):
    """Aggregated metrics for a skill across executions."""

    skill_id: str
    total_executions: int = 0
    successful: int = 0
    failed: int = 0
    avg_duration_ms: float = 0.0
    last_executed_at: datetime | None = None
    last_error: str | None = None
    feedback_summary: dict[str, int] = Field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        return self.successful / self.total_executions if self.total_executions else 0.0


class ImprovementProposal(BaseModel):
    """A proposal to evolve a skill. Composes with LearningManager (GAP-2).

    Status lifecycle (plain strings; stays independent of the learning
    subpackage): proposed → review_required → approved → applied,
    with reject / supersede branches.
    """

    proposal_id: str = Field(default_factory=lambda: new_id("skillprop"))
    skill_id: str
    current_version: str
    proposed_version: str | None = None  # None = same version, just a tweak
    change_description: str
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    source_feedback_ids: list[str] = Field(default_factory=list)
    source_learning_proposal_id: str | None = None  # link to LearningProposal
    metrics_snapshot: SkillMetrics | None = None
    status: str = "proposed"
    rejection_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    applied_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SkillEvolutionManager:
    """Tracks skill metrics + produces improvement proposals. Does NOT
    auto-apply. HITL invariant: PROPOSED → APPLIED is FORBIDDEN in apply()."""

    def __init__(
        self,
        skill_registry: SkillRegistry | Any,
        feedback_manager: Any | None = None,
    ) -> None:
        self._registry = skill_registry
        self._feedback = feedback_manager
        self._metrics: dict[str, SkillMetrics] = {}  # skill_id -> metrics
        self._proposals: dict[str, ImprovementProposal] = {}

    # ------------------------------------------------------------------
    # Metrics ingestion
    # ------------------------------------------------------------------
    async def record_execution(
        self,
        skill_id: str,
        success: bool,
        duration_ms: float,
        error: str | None = None,
    ) -> SkillMetrics:
        """Called by SkillExecutor after each execution. Updates the
        rolling metrics for `skill_id`. Returns the updated SkillMetrics."""
        m = self._metrics.get(skill_id)
        if m is None:
            m = SkillMetrics(skill_id=skill_id)
            self._metrics[skill_id] = m

        # Incremental rolling average: new_avg = old_avg + (x - old_avg)/n
        n = m.total_executions
        m.avg_duration_ms = (m.avg_duration_ms * n + duration_ms) / (n + 1)
        m.total_executions = n + 1
        if success:
            m.successful += 1
            # Do not overwrite a previously recorded last_error on success;
            # callers can clear it explicitly if desired.
        else:
            m.failed += 1
            m.last_error = error or "unknown error"
        m.last_executed_at = utc_now()
        return m

    async def record_feedback(self, skill_id: str, feedback_type: str) -> SkillMetrics:
        """Called when feedback is linked to a skill. `feedback_type`
        is the str value of a FeedbackType (or any domain-agnostic
        label) — stored as a count in `feedback_summary`."""
        m = self._metrics.get(skill_id)
        if m is None:
            m = SkillMetrics(skill_id=skill_id)
            self._metrics[skill_id] = m
        m.feedback_summary[feedback_type] = m.feedback_summary.get(feedback_type, 0) + 1
        return m

    async def get_metrics(self, skill_id: str) -> SkillMetrics | None:
        return self._metrics.get(skill_id)

    async def all_metrics(self) -> list[SkillMetrics]:
        return list(self._metrics.values())

    # ------------------------------------------------------------------
    # Proposal lifecycle
    # ------------------------------------------------------------------
    async def propose_improvement(
        self,
        *,
        skill_id: str,
        change_description: str,
        rationale: str,
        proposed_version: str | None = None,
        evidence_ids: list[str] | None = None,
        source_feedback_ids: list[str] | None = None,
        source_learning_proposal_id: str | None = None,
        snapshot_metrics: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new ImprovementProposal in `proposed` status.

        If `snapshot_metrics` is True and metrics exist for `skill_id`,
        a deep copy of the current SkillMetrics is attached to the
        proposal as evidence.
        """
        current_version = "unknown"
        try:
            contract: SkillContract = self._registry.get(skill_id)
            current_version = contract.version
        except Exception:
            # The skill may have been removed already; we still allow
            # the proposal to be created with current_version="unknown".
            pass

        metrics_snapshot: SkillMetrics | None = None
        if snapshot_metrics:
            m = self._metrics.get(skill_id)
            if m is not None:
                # Deep copy via pydantic model_dump round-trip so later
                # metrics updates don't mutate the snapshot.
                metrics_snapshot = SkillMetrics(**m.model_dump())

        proposal = ImprovementProposal(
            skill_id=skill_id,
            current_version=current_version,
            proposed_version=proposed_version,
            change_description=change_description,
            rationale=rationale,
            evidence_ids=list(evidence_ids or []),
            source_feedback_ids=list(source_feedback_ids or []),
            source_learning_proposal_id=source_learning_proposal_id,
            metrics_snapshot=metrics_snapshot,
            metadata=dict(metadata or {}),
        )
        self._proposals[proposal.proposal_id] = proposal
        return proposal.proposal_id

    async def request_review(self, proposal_id: str) -> bool:
        """PROPOSED → REVIEW_REQUIRED. Optional intermediate state."""
        p = self._proposals.get(proposal_id)
        if p is None or p.status != "proposed":
            return False
        p.status = "review_required"
        return True

    async def approve(self, proposal_id: str, reviewer: str) -> bool:
        """PROPOSED/REVIEW_REQUIRED/APPROVED → APPROVED. Accepts direct
        PROPOSED → APPROVED (some reviewers skip REVIEW_REQUIRED); the
        HITL gate is still enforced — apply() requires APPROVED status."""
        p = self._proposals.get(proposal_id)
        if p is None:
            return False
        if p.status not in ("proposed", "review_required", "approved"):
            return False
        p.status = "approved"
        p.reviewed_by = reviewer
        p.reviewed_at = utc_now()
        return True

    async def reject(self, proposal_id: str, reviewer: str, reason: str) -> bool:
        """Any pre-APPLIED → REJECTED. Records reviewer + reason + timestamp."""
        p = self._proposals.get(proposal_id)
        if p is None:
            return False
        if p.status in ("applied", "superseded"):
            return False
        p.status = "rejected"
        p.reviewed_by = reviewer
        p.reviewed_at = utc_now()
        p.rejection_reason = reason
        return True

    async def apply(self, proposal_id: str, applied_by: str) -> bool:
        """APPROVED → APPLIED. HITL gate enforced: raises
        SkillEvolutionError if status != APPROVED.

        Does NOT modify skill *code*. It only:
          1. Marks the proposal APPLIED (records applied_at + applied_by).
          2. If proposed_version is set, calls the existing
             SkillRegistry.upgrade_version() to bump the version
             (the handler code is left untouched — external dev applies
             the actual change). NOT a SkillRegistry.register() call.

        Idempotent on already-APPLIED proposals.
        """
        p = self._proposals.get(proposal_id)
        if p is None:
            raise SkillEvolutionError(
                f"Improvement proposal {proposal_id!r} not found; cannot apply."
            )
        if p.status == "proposed":
            raise SkillEvolutionError(
                f"Improvement proposal {proposal_id!r} is still PROPOSED; "
                "HITL requires APPROVED state before apply()."
            )
        if p.status == "review_required":
            raise SkillEvolutionError(
                f"Improvement proposal {proposal_id!r} is REVIEW_REQUIRED but "
                "not yet APPROVED; cannot apply without HITL approval."
            )
        if p.status == "rejected":
            raise SkillEvolutionError(
                f"Improvement proposal {proposal_id!r} was REJECTED; cannot apply."
            )
        if p.status == "superseded":
            raise SkillEvolutionError(
                f"Improvement proposal {proposal_id!r} was SUPERSEDED; cannot apply."
            )
        if p.status == "applied":
            return True  # idempotent
        if p.status != "approved":
            raise SkillEvolutionError(
                f"Improvement proposal {proposal_id!r} is in unexpected state "
                f"{p.status!r}; cannot apply."
            )

        # If proposed_version is set, delegate to the existing
        # SkillRegistry.upgrade_version() — bumps the contract version
        # + records a SkillLifecycle event. Does NOT touch handler code.
        if p.proposed_version is not None:
            try:
                self._registry.upgrade_version(p.skill_id, p.proposed_version)
            except Exception as exc:  # pragma: no cover - defensive
                raise SkillEvolutionError(
                    f"upgrade_version({p.skill_id!r}, {p.proposed_version!r}) "
                    f"failed: {exc}"
                ) from exc

        p.status = "applied"
        p.applied_at = utc_now()
        p.metadata["applied_by"] = applied_by
        return True

    async def supersede(self, proposal_id: str, new_proposal_id: str) -> bool:
        """Mark `proposal_id` as SUPERSEDED by `new_proposal_id`. The new
        proposal is NOT modified — it stays in its current status."""
        p = self._proposals.get(proposal_id)
        if p is None:
            return False
        if p.status in ("applied", "rejected", "superseded"):
            return False
        p.status = "superseded"
        p.metadata["superseded_by"] = new_proposal_id
        return True

    # ------------------------------------------------------------------
    # Read primitives
    # ------------------------------------------------------------------
    async def get_proposal(self, proposal_id: str) -> ImprovementProposal | None:
        return self._proposals.get(proposal_id)

    async def list_proposals(
        self,
        *,
        skill_id: str | None = None,
        status: str | None = None,
    ) -> list[ImprovementProposal]:
        out: list[ImprovementProposal] = []
        for p in self._proposals.values():
            if skill_id is not None and p.skill_id != skill_id:
                continue
            if status is not None and p.status != status:
                continue
            out.append(p)
        # Sort by created_at for deterministic ordering.
        out.sort(key=lambda p: p.created_at)
        return out


__all__ = [
    "SkillMetrics",
    "ImprovementProposal",
    "SkillEvolutionManager",
    "SkillEvolutionError",
]
