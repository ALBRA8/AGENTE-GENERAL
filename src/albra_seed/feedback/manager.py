"""FeedbackManager — high-level facade over a FeedbackStore.

Mirrors the role of `EvidenceManager` (over EvidenceStore) and `AuditLog`
(over AuditStore): a single entry point that:

  * Auto-correlates records via `CorrelationTracker.current_*()` when
    `execution_id` / `correlation_id` are not provided explicitly
    (same pattern as `audit/normal.py`).
  * Best-effort attaches provenance via `ProvenanceTracker` (try/except
    — non-fatal if the tracker is missing or unavailable). This keeps
    CORE_ONLY mode working with zero dependencies.
  * Provides convenience query and summary methods.

This manager is a SIBLING of EvidenceManager — they never share state
beyond what callers wire explicitly (e.g. evidence_ids in a feedback
record reference ids produced by an EvidenceManager). Feedback ids are
prefixed `fb_`, evidence ids `ev_`, audit ids `aud_` — the three stores
are independent.
"""

from __future__ import annotations

from typing import Any

from albra_seed.context.correlation import CorrelationTracker
from albra_seed.feedback.store import FeedbackStore, InMemoryFeedbackStore
from albra_seed.feedback.types import FeedbackRecord, FeedbackType


class FeedbackManager:
    """Single entry point for the Universal Feedback mechanism."""

    def __init__(
        self,
        store: FeedbackStore | None = None,
        *,
        provenance_tracker: Any | None = None,
    ) -> None:
        self.store: FeedbackStore = store or InMemoryFeedbackStore()
        # Optional ProvenanceTracker — best-effort; missing is OK.
        self._provenance_tracker = provenance_tracker

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    async def record(
        self,
        *,
        agent_id: str,
        feedback_type: FeedbackType,
        evaluation: str,
        source: str,
        source_type: str,
        expected: Any = None,
        actual: Any = None,
        execution_id: str | None = None,
        correlation_id: str | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        decision_id: str | None = None,
        skill_id: str | None = None,
        evidence_ids: list[str] | None = None,
        confidence: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record a feedback entry. Auto-correlates from context if the
        caller did not pass execution_id / correlation_id explicitly.
        Best-effort attaches a provenance record via ProvenanceTracker
        (silently skipped if the tracker is missing or fails).
        """
        # Auto-correlate from context (same pattern as AuditLog.record).
        if execution_id is None:
            execution_id = CorrelationTracker.current_execution_id()
        if correlation_id is None:
            correlation_id = CorrelationTracker.current_correlation_id()
        # If the agent_id was set in the correlation context and the
        # caller did not override it, prefer the context value.
        if agent_id is None:  # pragma: no cover - agent_id is required by type
            ctx_agent = CorrelationTracker.current_agent_id()
            if ctx_agent is not None:
                agent_id = ctx_agent

        record = FeedbackRecord(
            agent_id=agent_id,
            execution_id=execution_id,
            correlation_id=correlation_id,
            task_id=task_id,
            context_id=context_id,
            expected_outcome=expected,
            actual_outcome=actual,
            evaluation=evaluation,
            feedback_type=feedback_type,
            source=source,
            source_type=source_type,
            confidence=max(0.0, min(1.0, float(confidence))),
            evidence_ids=list(evidence_ids or []),
            decision_id=decision_id,
            skill_id=skill_id,
            metadata=metadata or {},
        )

        # Best-effort provenance — never fails the feedback write.
        provenance_id = await self._attach_provenance(record)
        if provenance_id is not None:
            record.provenance_id = provenance_id

        await self.store.write(record)
        return record.feedback_id

    async def _attach_provenance(self, record: FeedbackRecord) -> str | None:
        """Try to record a provenance entry for this feedback via the
        configured ProvenanceTracker. Returns the provenance_id, or None
        if no tracker is configured or the call failed.
        """
        tracker = self._provenance_tracker
        if tracker is None:
            return None
        try:
            # ProvenanceTracker exposes track_* builders; use the generic
            # `track` with a custom record_kind. The kind "feedback" is
            # not in the tracker's canonical KIND_* constants, which is
            # intentional — feedback provenance is best-effort and the
            # tracker accepts arbitrary kinds (see provenance/store.py).
            prov = await tracker.track(
                source=record.source,
                source_type=record.source_type,
                record_kind="feedback",
                record_ref=record.feedback_id,
                confidence=record.confidence,
                evidence_ids=record.evidence_ids,
                execution_id=record.execution_id,
                correlation_id=record.correlation_id,
            )
            return getattr(prov, "provenance_id", None)
        except Exception:
            # Provenance attachment must never break feedback recording.
            return None

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------
    async def get(self, feedback_id: str) -> FeedbackRecord | None:
        return await self.store.read(feedback_id)

    async def query(
        self,
        *,
        agent_id: str | None = None,
        execution_id: str | None = None,
        feedback_type: FeedbackType | None = None,
        limit: int = 50,
    ) -> list[FeedbackRecord]:
        return await self.store.query(
            agent_id=agent_id,
            execution_id=execution_id,
            feedback_type=feedback_type,
            limit=limit,
        )

    async def count(self) -> int:
        return await self.store.count()

    # ------------------------------------------------------------------
    # Linking
    # ------------------------------------------------------------------
    async def link_to_decision(
        self, feedback_id: str, decision_id: str
    ) -> bool:
        """Attach (or update) the decision_id of an existing feedback
        record. Returns True if the feedback existed and was updated.
        """
        record = await self.store.read(feedback_id)
        if record is None:
            return False
        record.decision_id = decision_id
        await self.store.write(record)
        return True

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    async def summary(self, *, agent_id: str) -> dict[FeedbackType, int]:
        """Return a count of feedback records per FeedbackType for the
        given agent. All six types are present in the result (zero for
        types with no records) so callers can iterate without guarding
        for KeyError.
        """
        records = await self.store.query(agent_id=agent_id, limit=10_000_000)
        counts: dict[FeedbackType, int] = dict.fromkeys(FeedbackType, 0)
        for rec in records:
            counts[rec.feedback_type] = counts.get(rec.feedback_type, 0) + 1
        return counts


__all__ = ["FeedbackManager"]
