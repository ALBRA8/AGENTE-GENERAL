"""A2AAdapter — pure translation between A2A protocol models and the
existing Handoff contract.

The adapter contains NO business logic. It only maps fields:

  A2ATask                            → HandoffRequest
  ───────                             ─────────────
  task_id                            → execution_id
  requester                          → requester
  target                             → target
  message.text (objective)           → objective
  {message, metadata}                → input
  {}                                 → constraints
  {}                                 → requested_output
  metadata.risk (if present)         → risk
  deadline                           → deadline
  correlation_id                     → correlation_id
  parent_execution_id                → parent_execution_id

  HandoffResponse                    → A2ATask
  ───────────────                      ───────
  execution_id                       → task_id
  status (succeeded/failed/...)      → state (COMPLETED/FAILED/...)
  result                             → artifacts[0]
  confidence                         → metadata.confidence
  warnings                           → metadata.warnings
  errors                             → metadata.errors
  evidence                           → metadata.evidence
"""

from __future__ import annotations

from typing import Any

from albra_seed.a2a.contract import (
    A2AArtifact,
    A2AMessage,
    A2ATask,
    TaskState,
)
from albra_seed.core.types import RiskLevel
from albra_seed.handoff.contract import HandoffRequest, HandoffResponse, HandoffStatus

# ---------------------------------------------------------------------
# Status mapping (handoff → a2a)
# ---------------------------------------------------------------------

_STATUS_MAP: dict[str, TaskState] = {
    HandoffStatus.PENDING: TaskState.SUBMITTED,
    HandoffStatus.ACCEPTED: TaskState.WORKING,
    HandoffStatus.IN_PROGRESS: TaskState.WORKING,
    HandoffStatus.SUCCEEDED: TaskState.COMPLETED,
    HandoffStatus.FAILED: TaskState.FAILED,
    HandoffStatus.REJECTED: TaskState.FAILED,
    HandoffStatus.TIMEOUT: TaskState.FAILED,
    HandoffStatus.CANCELLED: TaskState.CANCELED,
}


def _map_state(handoff_status: str) -> TaskState:
    """Map a HandoffStatus string → TaskState.

    Unknown statuses default to FAILED (defensive: never silently lie
    about a successful outcome).
    """
    return _STATUS_MAP.get(handoff_status, TaskState.FAILED)


class A2AAdapter:
    """Pure translation layer between A2A and Handoff contracts.

    The adapter holds no state. It only converts data shapes. All
    enforcement (firewall, policy, audit, evidence, etc.) happens in
    `A2ASecurityFilter` and the underlying `HandoffManager`.
    """

    def __init__(self, runtime: Any | None = None) -> None:
        # `runtime` is accepted for symmetry with other A2A components;
        # the adapter itself does not need it but may consult it for
        # future extensions (e.g. risk classification helpers).
        self._runtime = runtime

    # -----------------------------------------------------------------
    # A2ATask → HandoffRequest
    # -----------------------------------------------------------------
    def task_to_handoff_request(self, task: A2ATask) -> HandoffRequest:
        """Translate an A2ATask into a HandoffRequest.

        Preserves correlation_id, parent_execution_id and deadline so
        the downstream audit/provenance/evidence chains stay intact.
        """
        objective = task.objective or "(no message)"
        input_payload: dict[str, Any] = {
            "message": task.message.model_dump(mode="json") if task.message else None,
        }
        # Surface task metadata into the handoff input payload so the
        # receiving handler can inspect A2A-specific fields if needed.
        if task.metadata:
            input_payload["a2a_metadata"] = dict(task.metadata)

        # Risk classification: allow the caller to specify a risk level
        # via task.metadata["risk"]; default to UNKNOWN (the handoff
        # layer will classify via PolicyEngine if needed).
        risk = RiskLevel.UNKNOWN
        risk_value = task.metadata.get("risk") if task.metadata else None
        if isinstance(risk_value, str):
            try:
                risk = RiskLevel(risk_value)
            except ValueError:
                risk = RiskLevel.UNKNOWN

        return HandoffRequest(
            execution_id=task.task_id,
            requester=task.requester,
            target=task.target,
            objective=objective,
            input=input_payload,
            constraints={},
            requested_output={},
            risk=risk,
            deadline=task.deadline,
            correlation_id=task.correlation_id,
            parent_execution_id=task.parent_execution_id,
            metadata={
                "source": "a2a",
                "a2a_state": task.state.value,
            },
        )

    # -----------------------------------------------------------------
    # HandoffResponse → A2ATask
    # -----------------------------------------------------------------
    def handoff_response_to_task(
        self,
        response: HandoffResponse,
        original_task: A2ATask,
    ) -> A2ATask:
        """Translate a HandoffResponse back into an updated A2ATask.

        Builds artifacts from the response.result, propagates
        confidence / warnings / errors / evidence into the task's
        metadata so they remain visible to A2A clients without leaking
        HandoffResponse over the wire.
        """
        state = _map_state(response.status)

        # Build artifacts from the response result.
        artifacts: list[A2AArtifact] = list(original_task.artifacts)
        if response.result is not None:
            artifact = _result_to_artifact(response.result)
            if artifact is not None:
                artifacts.append(artifact)

        # Compose the response message (acknowledgement of completion).
        message = A2AMessage(
            role="assistant",
            parts=[
                {
                    "type": "text",
                    "text": _response_summary(response, state),
                }
            ],
            task_id=original_task.task_id,
        )

        # Propagate HandoffResponse metadata into A2ATask.metadata so
        # confidence / warnings / errors / evidence remain visible to
        # the A2A client without leaking the HandoffResponse shape.
        merged_metadata: dict[str, Any] = dict(original_task.metadata)
        merged_metadata.update(
            {
                "confidence": response.confidence,
                "warnings": list(response.warnings),
                "errors": list(response.errors),
                "evidence": list(response.evidence),
                "responder": response.responder,
                "handoff_status": response.status,
            }
        )

        return A2ATask(
            task_id=original_task.task_id,
            state=state,
            message=message,
            artifacts=artifacts,
            requester=original_task.requester,
            target=original_task.target,
            correlation_id=original_task.correlation_id,
            parent_execution_id=original_task.parent_execution_id,
            deadline=original_task.deadline,
            metadata=merged_metadata,
        )


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _result_to_artifact(result: Any) -> A2AArtifact | None:
    """Convert a HandoffResponse.result into an A2AArtifact.

    Handles dict / list / str / None / other-JSON-serializable shapes.
    Returns None only when result is None.
    """
    if result is None:
        return None
    if isinstance(result, str):
        return A2AArtifact.from_text(result, name="result")
    if isinstance(result, dict):
        # If the dict carries a "text" key, surface it as a text part;
        # otherwise serialize the whole dict as a data part.
        if "text" in result and isinstance(result["text"], str):
            parts = [{"type": "text", "text": str(result["text"])}]
        else:
            parts = [{"type": "data", "data": result}]
        return A2AArtifact(name="result", parts=parts)
    if isinstance(result, list):
        return A2AArtifact(
            name="result",
            parts=[{"type": "data", "data": result}],
        )
    # Fallback: stringify.
    return A2AArtifact.from_text(str(result), name="result")


def _response_summary(response: HandoffResponse, state: TaskState) -> str:
    """Short human-readable summary of the response for the A2A message."""
    if state == TaskState.COMPLETED:
        return "task completed"
    if state == TaskState.FAILED:
        errs = response.errors or ["unknown error"]
        return f"task failed: {errs[0]}"
    if state == TaskState.CANCELED:
        return "task canceled"
    if state == TaskState.INPUT_REQUIRED:
        return "task requires additional input"
    if state == TaskState.WORKING:
        return "task in progress"
    return f"task state: {state.value}"


__all__ = ["A2AAdapter"]
