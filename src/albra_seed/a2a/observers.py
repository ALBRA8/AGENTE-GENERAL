"""A2A observers — audit / provenance / evidence helpers used by A2AServer.

These helpers are split out of `server.py` to keep that file focused on
the receive/get_status/cancel lifecycle. Each helper LAZILY resolves its
target component from the runtime and silently no-ops when the
component is absent (CORE_ONLY mode).

Audit failures NEVER break the A2A path — they are suppressed via
`contextlib.suppress(Exception)`.
"""

from __future__ import annotations

import contextlib
from typing import Any

from albra_seed.a2a.contract import A2ATask
from albra_seed.handoff.contract import HandoffResponse
from albra_seed.runtime.runtime import (
    COMPONENT_AUDIT_LOGGER,
    COMPONENT_EVIDENCE_COLLECTOR,
)

A2A_RECEIVE_ACTION = "a2a.task.receive"


async def record_audit(
    runtime: Any,
    task: A2ATask,
    *,
    result: str = "success",
    error: str | None = None,
    approval_id: str | None = None,
) -> None:
    """Append an `a2a.task.receive` entry to the audit log (if any)."""
    audit_logger = runtime.get_component(COMPONENT_AUDIT_LOGGER)
    if audit_logger is None:
        return
    payload = {
        "action": A2A_RECEIVE_ACTION,
        "actor": task.requester,
        "result": result,
        "error": error,
        "correlation_id": task.correlation_id,
        "execution_id": task.task_id,
        "approval_id": approval_id,
        "metadata": {"target": task.target, "state": task.state.value},
    }
    with contextlib.suppress(Exception):
        if hasattr(audit_logger, "record_async"):
            await audit_logger.record_async(**payload)
        elif hasattr(audit_logger, "record"):
            audit_logger.record(**payload)


async def track_input_provenance(runtime: Any, task: A2ATask) -> None:
    """Record an input provenance entry for the inbound task."""
    tracker = runtime.get_component("provenance_tracker")
    if tracker is None:
        return
    with contextlib.suppress(Exception):
        await tracker.track_input(
            source=task.requester,
            record_ref=task.task_id,
            execution_id=task.task_id,
            correlation_id=task.correlation_id,
        )


async def track_output_provenance(
    runtime: Any, task: A2ATask, response: HandoffResponse
) -> None:
    """Record an output provenance entry for the dispatched task."""
    tracker = runtime.get_component("provenance_tracker")
    if tracker is None:
        return
    with contextlib.suppress(Exception):
        await tracker.track_output(
            source=task.target,
            record_ref=task.task_id,
            execution_id=task.task_id,
            correlation_id=task.correlation_id,
            confidence=response.confidence,
        )


async def register_evidence(
    runtime: Any, task: A2ATask, response: HandoffResponse
) -> None:
    """Register an evidence record for the response and surface its id."""
    evidence_manager = runtime.get_component(COMPONENT_EVIDENCE_COLLECTOR)
    if evidence_manager is None:
        return
    with contextlib.suppress(Exception):
        evidence = await evidence_manager.register(
            source=task.target,
            source_type="agent",
            content=response.result,
            execution_id=task.task_id,
            confidence=response.confidence,
        )
        ev_list = task.metadata.setdefault("evidence_ids", [])
        ev_list.append(evidence.evidence_id)


__all__ = [
    "record_audit",
    "track_input_provenance",
    "track_output_provenance",
    "register_evidence",
    "A2A_RECEIVE_ACTION",
]
