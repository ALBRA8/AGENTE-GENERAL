"""LearningManager — orchestrates the proposal lifecycle with HITL.

The manager is the *actor* of the Universal Learning pipeline. It owns a
`LearningStore`, walks each `LearningProposal` through its lifecycle, and
enforces the HITL invariant:

    PROPOSED → REVIEW_REQUIRED → APPROVED → APPLIED

CRITICAL design rules:
  * `apply()` MUST NOT auto-modify any code, skill or memory. It only
    marks the proposal as APPLIED and records `applied_at`. The actual
    change happens EXTERNALLY (a human/dev applies it); the Learning
    system just tracks the lifecycle.
  * PROPOSED → APPLIED directly is FORBIDDEN. `apply()` only succeeds
    when status == APPROVED — this is the HITL gate.
  * CorrelationTracker is reused best-effort (non-fatal if absent).
  * ProvenanceTracker is reused best-effort (try/except) — same pattern
    as FeedbackManager.

The manager is a SIBLING of FeedbackManager / DecisionMemory — they
share the same correlation discipline but the LearningStore is an
independent persistence space (id prefix `learn_`).
"""

from __future__ import annotations

import contextlib
from typing import Any

from albra_seed.context.correlation import CorrelationTracker
from albra_seed.core.types import utc_now
from albra_seed.learning.store import InMemoryLearningStore, LearningStore
from albra_seed.learning.types import (
    LearningProposal,
    ProposalStatus,
)


class LearningError(RuntimeError):
    """Raised when a lifecycle transition is invalid."""


class LearningManager:
    """Single entry point for the Universal Learning lifecycle.

    Holds a `LearningStore` and exposes the lifecycle transitions:

        propose()         — register a PROPOSED proposal.
        request_review()  — PROPOSED → REVIEW_REQUIRED.
        approve()         — REVIEW_REQUIRED → APPROVED.
        reject()          — any pre-APPLIED → REJECTED (records reason).
        apply()           — APPROVED → APPLIED (HITL gate enforced).
        supersede()       — old proposal → SUPERSEDED.

    Plus read primitives: `get()`, `query()`.
    """

    def __init__(
        self,
        store: LearningStore | None = None,
        *,
        provenance_tracker: Any | None = None,
    ) -> None:
        self.store: LearningStore = store or InMemoryLearningStore()
        # Optional ProvenanceTracker — best-effort; missing is OK.
        self._provenance_tracker = provenance_tracker

    # ------------------------------------------------------------------
    # Write: propose
    # ------------------------------------------------------------------
    async def propose(self, proposal: LearningProposal) -> str:
        """Register a proposal in PROPOSED status.

        The proposal is normalised: status is forced to PROPOSED and
        applied_at / reviewed_at / reviewed_by / rejection_reason are
        cleared so a caller cannot smuggle in a pre-approved record.
        """
        # Auto-correlate from context (best-effort) if missing.
        if proposal.execution_id is None:
            with contextlib.suppress(Exception):
                proposal.execution_id = CorrelationTracker.current_execution_id()
        if proposal.correlation_id is None:
            with contextlib.suppress(Exception):
                proposal.correlation_id = CorrelationTracker.current_correlation_id()

        # Force lifecycle fields to a clean PROPOSED state.
        proposal.status = ProposalStatus.PROPOSED
        proposal.reviewed_at = None
        proposal.reviewed_by = None
        proposal.applied_at = None
        proposal.rejection_reason = None

        # Best-effort provenance — never fails the proposal write.
        await self._attach_provenance(proposal)

        await self.store.write(proposal)
        return proposal.proposal_id

    async def _attach_provenance(self, proposal: LearningProposal) -> None:
        """Try to record a provenance entry via the configured
        ProvenanceTracker. Non-fatal — silently skipped if no tracker
        or the call fails."""
        tracker = self._provenance_tracker
        if tracker is None:
            return
        try:
            await tracker.track(
                source="learning-manager",
                source_type="system",
                record_kind="learning_proposal",
                record_ref=proposal.proposal_id,
                confidence=proposal.confidence,
                evidence_ids=list(proposal.evidence_ids),
                execution_id=proposal.execution_id,
                correlation_id=proposal.correlation_id,
            )
        except Exception:
            # Provenance attachment must never break proposal recording.
            return

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------
    async def request_review(self, proposal_id: str) -> bool:
        """Transition PROPOSED → REVIEW_REQUIRED. Returns True on success,
        False if the proposal was not found or not in PROPOSED state."""
        rec = await self.store.read(proposal_id)
        if rec is None or rec.status != ProposalStatus.PROPOSED:
            return False
        return await self.store.update(
            proposal_id,
            status=ProposalStatus.REVIEW_REQUIRED,
            reviewed_at=None,  # review is requested, not completed
        )

    async def approve(self, proposal_id: str, approver: str) -> bool:
        """Transition REVIEW_REQUIRED → APPROVED. Records approver + timestamp.
        Returns False if not found or not in REVIEW_REQUIRED state.

        We accept APPROVED → APPROVED as a no-op idempotent transition
        (re-approval by a different approver updates reviewed_by). This
        is intentional — a human may re-affirm an approval.
        """
        rec = await self.store.read(proposal_id)
        if rec is None:
            return False
        if rec.status not in (ProposalStatus.REVIEW_REQUIRED, ProposalStatus.APPROVED):
            return False
        return await self.store.update(
            proposal_id,
            status=ProposalStatus.APPROVED,
            reviewed_by=approver,
            reviewed_at=utc_now(),
        )

    async def reject(
        self, proposal_id: str, approver: str, reason: str
    ) -> bool:
        """Transition any pre-APPLIED proposal → REJECTED. Records
        approver + reason + timestamp. Returns False if the proposal
        was not found or already APPLIED/SUPERSEDED (cannot reject
        something already applied)."""
        rec = await self.store.read(proposal_id)
        if rec is None:
            return False
        if rec.status in (ProposalStatus.APPLIED, ProposalStatus.SUPERSEDED):
            return False
        return await self.store.update(
            proposal_id,
            status=ProposalStatus.REJECTED,
            reviewed_by=approver,
            reviewed_at=utc_now(),
            rejection_reason=reason,
        )

    async def apply(self, proposal_id: str, applied_by: str) -> bool:
        """Transition APPROVED → APPLIED.

        CRITICAL: This method MUST NOT auto-modify any code, skill or
        memory. It only marks the proposal as APPLIED and records the
        timestamp + who marked it. The actual change is applied externally
        (by a human/dev process) — the Learning system just tracks the
        lifecycle.

        HITL gate: PROPOSED → APPLIED directly is FORBIDDEN. This method
        raises `LearningError` if the proposal is not in APPROVED state.
        Returns True on success.
        """
        rec = await self.store.read(proposal_id)
        if rec is None:
            raise LearningError(
                f"Learning proposal {proposal_id!r} not found; cannot apply."
            )
        if rec.status == ProposalStatus.PROPOSED:
            raise LearningError(
                f"Learning proposal {proposal_id!r} is still PROPOSED; "
                "HITL requires APPROVED state before apply()."
            )
        if rec.status == ProposalStatus.REVIEW_REQUIRED:
            raise LearningError(
                f"Learning proposal {proposal_id!r} is REVIEW_REQUIRED but "
                "not yet APPROVED; cannot apply without HITL approval."
            )
        if rec.status == ProposalStatus.REJECTED:
            raise LearningError(
                f"Learning proposal {proposal_id!r} was REJECTED; cannot apply."
            )
        if rec.status == ProposalStatus.SUPERSEDED:
            raise LearningError(
                f"Learning proposal {proposal_id!r} was SUPERSEDED; cannot apply."
            )
        if rec.status == ProposalStatus.APPLIED:
            # Idempotent: re-applying an already-applied proposal is a no-op
            # that returns True (the proposal IS applied).
            return True
        if rec.status != ProposalStatus.APPROVED:
            raise LearningError(
                f"Learning proposal {proposal_id!r} is in unexpected state "
                f"{rec.status!r}; cannot apply."
            )

        now = utc_now()
        ok = await self.store.update(
            proposal_id,
            status=ProposalStatus.APPLIED,
            applied_at=now,
            # Preserve the original approver in reviewed_by; record applied_by
            # in metadata if different from reviewed_by.
            metadata={**rec.metadata, "applied_by": applied_by, "applied_at": now.isoformat()},
        )
        return ok

    async def supersede(
        self, proposal_id: str, new_proposal_id: str
    ) -> bool:
        """Mark `proposal_id` as SUPERSEDED by `new_proposal_id`. The
        new proposal is NOT modified — it stays in its current status.

        Returns False if the old proposal is not found or already in a
        terminal state (APPLIED/REJECTED/SUPERSEDED).
        """
        rec = await self.store.read(proposal_id)
        if rec is None:
            return False
        if rec.status in (
            ProposalStatus.APPLIED,
            ProposalStatus.REJECTED,
            ProposalStatus.SUPERSEDED,
        ):
            return False
        return await self.store.update(
            proposal_id,
            status=ProposalStatus.SUPERSEDED,
            metadata={**rec.metadata, "superseded_by": new_proposal_id},
        )

    # ------------------------------------------------------------------
    # Read primitives
    # ------------------------------------------------------------------
    async def get(self, proposal_id: str) -> LearningProposal | None:
        return await self.store.read(proposal_id)

    async def query(
        self,
        *,
        agent_id: str | None = None,
        status: ProposalStatus | None = None,
        target_component: str | None = None,
        limit: int = 50,
    ) -> list[LearningProposal]:
        return await self.store.query(
            agent_id=agent_id,
            status=status,
            target_component=target_component,
            limit=limit,
        )

    async def count(self) -> int:
        return await self.store.count()

    # ------------------------------------------------------------------
    # Convenience: lifecycle introspection
    # ------------------------------------------------------------------
    async def pending_review(
        self, *, agent_id: str | None = None, limit: int = 50
    ) -> list[LearningProposal]:
        """Return proposals in REVIEW_REQUIRED status (optionally per agent)."""
        return await self.query(
            agent_id=agent_id,
            status=ProposalStatus.REVIEW_REQUIRED,
            limit=limit,
        )

    async def approved_pending_apply(
        self, *, agent_id: str | None = None, limit: int = 50
    ) -> list[LearningProposal]:
        """Return proposals that are APPROVED but not yet APPLIED."""
        recs = await self.query(
            agent_id=agent_id,
            status=ProposalStatus.APPROVED,
            limit=limit,
        )
        return [r for r in recs if r.applied_at is None]


__all__ = ["LearningManager", "LearningError"]
