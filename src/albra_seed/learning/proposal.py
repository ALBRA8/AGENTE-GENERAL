"""ProposalGenerator — turns observations into LearningProposal records.

The generator is the *sensor* of the Universal Learning pipeline. It reads
from the existing feedback, decision-memory and execution subsystems and
synthesises `LearningProposal` records when patterns warrant. The
`LearningManager` is the *actor* that walks each proposal through its
HITL lifecycle.

This module produces ONLY `LearningProposal` instances — it does NOT
persist them. The caller decides whether to feed them into a
LearningManager. This keeps the generator trivially testable.

Design rules (CRITICAL):
  * NO ML training — the generator uses simple aggregation rules.
  * NO auto-modification of code — proposals are inert records.
  * CorrelationTracker is reused best-effort.
  * Lazy import of FeedbackManager / DecisionMemory / ExecutionTrace
    keeps the module robust to future refactors and avoids any circular
    import risk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from albra_seed.context.correlation import CorrelationTracker
from albra_seed.learning.types import (
    LEARNING_TARGET_COMPONENTS,
    LearningProposal,
    LearningSignal,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from albra_seed.execution.trace import ExecutionTrace
    from albra_seed.feedback.manager import FeedbackManager
    from albra_seed.memory.decision_store import DecisionMemory


# Keywords that, when present in a DecisionRecord.outcome, indicate the
# outcome did not match expectation. Lowercased match.
_OUTCOME_NEGATIVE_KEYWORDS: tuple[str, ...] = (
    "fail", "failed", "wrong", "incorrect", "error",
    "rejected", "rollback", "mismatch", "timeout", "exception",
)


def _infer_target_component(rec: Any) -> str:
    """Infer the target_component for a feedback record.

    Priority:
      1. rec.metadata["target_component"] if it is a known value.
      2. rec.skill_id → "skill".
      3. rec.metadata["target"] if it is a known value.
      4. "other".
    """
    md = getattr(rec, "metadata", None) or {}
    if isinstance(md, dict):
        explicit = md.get("target_component")
        if isinstance(explicit, str) and explicit in LEARNING_TARGET_COMPONENTS:
            return explicit
        target = md.get("target")
        if isinstance(target, str) and target in LEARNING_TARGET_COMPONENTS:
            return target
    skill_id = getattr(rec, "skill_id", None)
    if skill_id:
        return "skill"
    return "other"


def _safe_correlation() -> tuple[str | None, str | None]:
    """Return (execution_id, correlation_id) from the current context.
    Best-effort: returns (None, None) if the tracker has no scope set."""
    try:
        return (
            CorrelationTracker.current_execution_id(),
            CorrelationTracker.current_correlation_id(),
        )
    except Exception:
        return None, None


def _merge_unique(lists: list[list[Any]]) -> list[str]:
    """Flatten a list-of-lists into a deduped list of strings, preserving
    first-seen order. Non-string items are skipped."""
    out: list[str] = []
    seen: set[str] = set()
    for src in lists:
        for item in src or []:
            if not isinstance(item, str):
                continue
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def _collect_evidence(records: list[Any]) -> list[str]:
    """Collect evidence_ids from a list of records with .evidence_ids."""
    return _merge_unique(
        [list(getattr(r, "evidence_ids", None) or []) for r in records]
    )


def _avg_confidence(records: list[Any]) -> float:
    if not records:
        return 0.0
    total = 0.0
    n = 0
    for r in records:
        c = getattr(r, "confidence", None)
        if isinstance(c, (int, float)):
            total += float(c)
            n += 1
    if n == 0:
        return 0.0
    return max(0.0, min(1.0, total / n))


def _collect_decision_ids(records: list[Any]) -> list[str]:
    """Decision ids from feedback records, deduped, order-preserving."""
    out: list[str] = []
    seen: set[str] = set()
    for r in records:
        did = getattr(r, "decision_id", None)
        if did and did not in seen:
            seen.add(did)
            out.append(did)
    return out


def _collect_execution_ids(records: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for r in records:
        eid = getattr(r, "execution_id", None)
        if eid and eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


class ProposalGenerator:
    """Synthesises LearningProposal records from existing observations.

    All three entrypoints are async (they read from async stores). Each
    returns a single proposal OR None if the triggering condition was not
    met. The generator NEVER persists the proposal — that is the
    LearningManager's job.
    """

    # ------------------------------------------------------------------
    # Signal 1: feedback aggregation
    # ------------------------------------------------------------------
    async def from_feedback(
        self,
        *,
        agent_id: str,
        feedback_ids: list[str],
        feedback_manager: FeedbackManager,
    ) -> LearningProposal | None:
        """Produce a proposal when ≥3 feedback records of the same
        FeedbackType point to the same target_component.

        Returns None otherwise.
        """
        if not feedback_ids or len(feedback_ids) < 3:
            return None

        records: list[Any] = []
        for fid in feedback_ids:
            rec = await feedback_manager.get(fid)
            if rec is not None:
                records.append(rec)
        if len(records) < 3:
            return None

        # Group by (feedback_type, target_component) — pick the dominant group.
        groups: dict[tuple[str, str], list[Any]] = {}
        for r in records:
            key = (r.feedback_type.value, _infer_target_component(r))
            groups.setdefault(key, []).append(r)
        # Choose the largest group; tie-break by first-seen order.
        dominant_key, dominant = max(
            groups.items(), key=lambda kv: (len(kv[1]), -records.index(kv[1][0]))
        )
        if len(dominant) < 3:
            return None

        fb_type_str, target_component = dominant_key
        snippets = [getattr(r, "evaluation", "") for r in dominant]
        observation = (
            f"{len(dominant)} {fb_type_str} feedback records targeting "
            f"'{target_component}': " + " | ".join(snippets)[:500]
        )
        proposed_change = (
            f"Review and revise {target_component} behaviour based on "
            f"{len(dominant)} {fb_type_str} feedback signals."
        )
        rationale = (
            "Repeated feedback of the same type against the same target "
            "is a strong indicator of a systematic issue rather than a "
            "one-off error."
        )

        exec_id, corr_id = _safe_correlation()
        if exec_id is None:
            exec_ids = _collect_execution_ids(dominant)
            if exec_ids:
                exec_id = exec_ids[0]
        if corr_id is None:
            corr_ids = [
                getattr(r, "correlation_id", None) for r in dominant
            ]
            corr_id = next((c for c in corr_ids if c), None)

        target_id = next(
            (getattr(r, "skill_id", None) for r in dominant if getattr(r, "skill_id", None)),
            None,
        )

        return LearningProposal(
            agent_id=agent_id,
            signal_type=LearningSignal.FEEDBACK,
            source_feedback_ids=[r.feedback_id for r in dominant],
            source_decision_ids=_collect_decision_ids(dominant),
            source_execution_ids=_collect_execution_ids(dominant),
            observation=observation,
            proposed_change=proposed_change,
            rationale=rationale,
            target_component=target_component,
            target_id=target_id,
            confidence=_avg_confidence(dominant),
            evidence_ids=_collect_evidence(dominant),
            execution_id=exec_id,
            correlation_id=corr_id,
        )

    # ------------------------------------------------------------------
    # Signal 2: decision outcome mismatch
    # ------------------------------------------------------------------
    async def from_decision_outcome(
        self,
        *,
        agent_id: str,
        decision_id: str,
        decision_memory: DecisionMemory,
        feedback_manager: FeedbackManager,
    ) -> LearningProposal | None:
        """Produce a proposal when a decision's observed outcome does not
        match expectation.

        A mismatch is signalled when ANY of:
          * The decision.outcome contains a negative keyword
            (fail/wrong/error/...).
          * A linked FeedbackRecord has type NEGATIVE or CORRECTION.
          * A linked FeedbackRecord has expected_outcome != actual_outcome.
        """
        decision = await decision_memory.get_decision(decision_id)
        if decision is None:
            return None

        # Gather linked feedback records (best-effort).
        linked_feedback: list[Any] = []
        for fid in decision.feedback_ids or []:
            rec = await feedback_manager.get(fid)
            if rec is not None:
                linked_feedback.append(rec)

        outcome_text = (decision.outcome or "").lower()
        outcome_negative = any(
            kw in outcome_text for kw in _OUTCOME_NEGATIVE_KEYWORDS
        )

        feedback_negative = any(
            getattr(r, "feedback_type", None)
            and r.feedback_type.value in {"negative", "correction"}
            for r in linked_feedback
        )
        outcome_mismatch = any(
            getattr(r, "expected_outcome", None) is not None
            and getattr(r, "expected_outcome", None)
            != getattr(r, "actual_outcome", None)
            for r in linked_feedback
        )

        if not (outcome_negative or feedback_negative or outcome_mismatch):
            return None

        if outcome_negative:
            observation = (
                f"Decision {decision.decision_id} outcome '{decision.outcome}' "
                "indicates the chosen option did not produce the expected result."
            )
        elif feedback_negative:
            observation = (
                f"Decision {decision.decision_id} received negative/correction "
                "feedback after execution."
            )
        else:
            observation = (
                f"Decision {decision.decision_id} has feedback where expected "
                "outcome does not match actual outcome."
            )

        proposed_change = (
            f"Update the policy/skill that produced decision "
            f"'{decision.decision_taken}' so this class of decision avoids "
            "the observed failure mode."
        )
        rationale = (
            "Decision outcome evaluation revealed a gap between expected and "
            "actual results — a proposal is warranted."
        )
        target_component = "skill" if not decision.evidence_ids else "policy"

        return LearningProposal(
            agent_id=agent_id,
            signal_type=LearningSignal.OUTCOME,
            source_decision_ids=[decision_id],
            source_feedback_ids=[r.feedback_id for r in linked_feedback],
            source_execution_ids=(
                [decision.execution_id] if decision.execution_id else []
            ),
            observation=observation,
            proposed_change=proposed_change,
            rationale=rationale,
            target_component=target_component,
            target_id=None,
            confidence=max(decision.confidence, _avg_confidence(linked_feedback)),
            evidence_ids=_collect_evidence(linked_feedback + [decision]),
            execution_id=decision.execution_id,
            correlation_id=decision.correlation_id,
        )

    # ------------------------------------------------------------------
    # Signal 3: execution trace errors/warnings
    # ------------------------------------------------------------------
    async def from_execution_trace(
        self,
        *,
        agent_id: str,
        execution_id: str,
        trace: ExecutionTrace,
        feedback_manager: FeedbackManager,
    ) -> LearningProposal | None:
        """Produce a proposal when an execution trace contains errors
        and/or warnings. Returns None if the trace is clean."""
        errors = list(getattr(trace, "errors", None) or [])
        warnings = list(getattr(trace, "warnings", None) or [])
        if not errors and not warnings:
            return None

        # Pull any feedback already linked to this execution (best-effort).
        try:
            linked = await feedback_manager.query(
                agent_id=agent_id, execution_id=execution_id, limit=50
            )
        except Exception:
            linked = []

        if errors:
            observation = (
                f"Execution {execution_id} produced {len(errors)} error(s): "
                + "; ".join(errors)[:500]
            )
            target_component = "tool"
        else:
            observation = (
                f"Execution {execution_id} produced {len(warnings)} warning(s): "
                + "; ".join(warnings)[:500]
            )
            target_component = "prompt"

        proposed_change = (
            "Add a recovery strategy or refine the execution path so this "
            "class of error/warning is avoided or handled gracefully."
        )
        rationale = (
            "Execution traces with errors or warnings are direct signals "
            "that the underlying skill/tool/policy needs improvement."
        )

        trace_evidence = list(getattr(trace, "evidence_ids", None) or [])
        all_evidence = _merge_unique(
            [trace_evidence]
            + [list(getattr(r, "evidence_ids", None) or []) for r in linked]
        )

        return LearningProposal(
            agent_id=agent_id,
            signal_type=LearningSignal.EXPERIENCE,
            source_feedback_ids=[r.feedback_id for r in linked],
            source_decision_ids=[],
            source_execution_ids=[execution_id],
            observation=observation,
            proposed_change=proposed_change,
            rationale=rationale,
            target_component=target_component,
            target_id=None,
            confidence=max(0.5, _avg_confidence(linked)),
            evidence_ids=all_evidence,
            execution_id=execution_id,
            correlation_id=getattr(trace, "correlation_id", None),
        )


__all__ = ["ProposalGenerator"]
