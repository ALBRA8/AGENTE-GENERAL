"""A2A 1.0 wire-format serialization.

Translates ALBRA A2A models to/from the A2A 1.0 JSON-RPC wire format
defined by the Google agent2agent protocol specification.

The ALBRA models (AgentCard, A2ATask, A2AMessage, A2AArtifact) are the
internal representation. The wire format is the on-the-wire JSON-RPC
envelope defined by A2A 1.0. This module bridges them — the single
source of truth stays with the ALBRA models; the wire format is a
purely derivative projection.

NON-BREAKING: the existing pydantic models are NOT modified. This
module only adds serialization helpers that callers may opt into.

Reference: https://github.com/google/agent2agent (A2A 1.0)
"""

from __future__ import annotations

from typing import Any

from albra_seed.a2a.card import A2ACardSkill, AgentCard
from albra_seed.a2a.contract import (
    A2AArtifact,
    A2AMessage,
    A2ATask,
    A2ATaskStatus,
    TaskState,
)
from albra_seed.core.types import new_id

# ---------------------------------------------------------------------
# State mapping (ALBRA enum → A2A 1.0 wire kebab-case strings)
# ---------------------------------------------------------------------

WIRE_STATE_MAP: dict[TaskState, str] = {
    TaskState.SUBMITTED: "submitted",
    TaskState.WORKING: "working",
    TaskState.INPUT_REQUIRED: "input-required",
    TaskState.COMPLETED: "completed",
    TaskState.CANCELED: "canceled",
    TaskState.FAILED: "failed",
}
WIRE_STATE_REVERSE: dict[str, TaskState] = {v: k for k, v in WIRE_STATE_MAP.items()}


# ---------------------------------------------------------------------
# AgentCard ↔ wire
# ---------------------------------------------------------------------


def agent_card_to_wire(card: AgentCard) -> dict[str, Any]:
    """Serialize an AgentCard → A2A 1.0 wire-format dict.

    Field renames: ``endpoint`` → ``url``, ``authentication`` →
    ``securitySchemes`` + ``security``, ``capabilities:list`` →
    ``capabilities:object`` (spec shape). The original ALBRA list is
    preserved under ``x-albra-metadata`` so round-trip is lossless.
    """
    auth = card.authentication or {}
    schemes = auth.get("schemes", {})
    if isinstance(schemes, list):
        # Spec wants securitySchemes as an object keyed by scheme name.
        schemes = {f"scheme-{i}": s for i, s in enumerate(schemes)}
    return {
        "name": card.name,
        "description": card.description,
        "version": card.version,
        "url": card.endpoint or "",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": list(card.default_input_modes),
        "defaultOutputModes": list(card.default_output_modes),
        "skills": [_skill_to_wire(s) for s in card.skills],
        "securitySchemes": schemes,
        "security": list(auth.get("security", [])),
        "provider": dict(card.provider or {}),
        # Extension metadata (ALBRA-specific, ignored by pure A2A 1.0
        # clients). Preserves the original capabilities list + metadata
        # dict so wire_to_agent_card can round-trip losslessly.
        "x-albra-metadata": {
            **dict(card.metadata or {}),
            "protocol_version": card.protocol_version,
            "capabilities_list": list(card.capabilities),
            "generated_at": card.generated_at,
        },
    }


def wire_to_agent_card(data: dict[str, Any]) -> AgentCard:
    """Deserialize an A2A 1.0 wire dict → AgentCard.

    Tolerant of missing keys: every field has a sensible default so a
    minimally-populated wire card (just ``name``) still round-trips.
    """
    meta = dict(data.get("x-albra-metadata") or {})
    skills_wire = data.get("skills") or []
    skills = [_skill_from_wire(s) for s in skills_wire]
    schemes = data.get("securitySchemes") or {}
    security = list(data.get("security") or [])
    auth: dict[str, Any] = {}
    if schemes or security:
        auth["schemes"] = schemes
        auth["security"] = security
    return AgentCard(
        protocol_version=str(meta.get("protocol_version", "1.0")),
        name=data.get("name", ""),
        description=data.get("description", ""),
        version=data.get("version", "1.0.0"),
        endpoint=data.get("url") or data.get("endpoint"),
        capabilities=list(meta.get("capabilities_list") or []),
        skills=skills,
        default_input_modes=list(data.get("defaultInputModes") or ["text"]),
        default_output_modes=list(data.get("defaultOutputModes") or ["text"]),
        authentication=auth,
        provider=dict(data.get("provider") or {}),
        metadata={k: v for k, v in meta.items()
                  if k not in {"protocol_version", "capabilities_list", "generated_at"}},
    )


def _skill_to_wire(s: A2ACardSkill) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "tags": list(s.tags),
        "inputModes": list(s.input_modes),
        "outputModes": list(s.output_modes),
    }


def _skill_from_wire(d: dict[str, Any]) -> A2ACardSkill:
    return A2ACardSkill(
        id=d.get("id", ""),
        name=d.get("name", ""),
        description=d.get("description", ""),
        tags=list(d.get("tags") or []),
        input_modes=list(d.get("inputModes") or ["text"]),
        output_modes=list(d.get("outputModes") or ["text"]),
    )


# ---------------------------------------------------------------------
# Message / Artifact ↔ wire
# ---------------------------------------------------------------------


def message_to_wire(msg: A2AMessage) -> dict[str, Any]:
    """A2AMessage → A2A 1.0 wire Message dict.

    Field renames: ``message_id`` → ``messageId``, ``task_id`` → ``taskId``.
    """
    return {
        "role": msg.role,
        "parts": list(msg.parts),
        "messageId": msg.message_id,
        "taskId": msg.task_id,
    }


def wire_to_message(data: dict[str, Any]) -> A2AMessage:
    """Wire Message dict → A2AMessage (tolerant of missing keys)."""
    return A2AMessage(
        role=str(data.get("role", "user")),
        parts=list(data.get("parts") or []),
        message_id=str(data.get("messageId") or data.get("message_id") or new_id("msg")),
        task_id=data.get("taskId") or data.get("task_id"),
    )


def artifact_to_wire(a: A2AArtifact) -> dict[str, Any]:
    """A2AArtifact → A2A 1.0 wire Artifact dict."""
    return {
        "name": a.name,
        "description": a.description,
        "parts": list(a.parts),
        "artifactId": a.artifact_id,
    }


def wire_to_artifact(data: dict[str, Any]) -> A2AArtifact:
    """Wire Artifact dict → A2AArtifact (tolerant of missing keys)."""
    return A2AArtifact(
        name=str(data.get("name", "")),
        description=str(data.get("description", "")),
        parts=list(data.get("parts") or []),
        artifact_id=str(data.get("artifactId") or data.get("artifact_id") or new_id("art")),
    )


# ---------------------------------------------------------------------
# Task / TaskStatus ↔ wire
# ---------------------------------------------------------------------


def task_to_wire(task: A2ATask) -> dict[str, Any]:
    """A2ATask → A2A 1.0 wire Task dict.

    Uses spec field names: ``id`` (not ``task_id``), ``status`` (not
    ``state``), ``sessionId`` (extracted from metadata).
    """
    msg_wire = message_to_wire(task.message) if task.message else None
    state_wire = WIRE_STATE_MAP.get(task.state, "submitted")
    metadata = dict(task.metadata or {})
    session_id = metadata.pop("session_id", None)
    updated_at = metadata.get("updated_at")
    return {
        "id": task.task_id,
        "sessionId": session_id,
        "status": {
            "state": state_wire,
            "timestamp": updated_at,
            "message": msg_wire,
        },
        "message": msg_wire,
        "artifacts": [artifact_to_wire(a) for a in task.artifacts],
        "metadata": metadata,
    }


def wire_to_task(data: dict[str, Any]) -> A2ATask:
    """A2A 1.0 wire dict → A2ATask (tolerant of missing keys)."""
    status = data.get("status") or {}
    state_str = status.get("state") or data.get("state") or "submitted"
    state = WIRE_STATE_REVERSE.get(state_str, TaskState.SUBMITTED)
    msg_data = data.get("message") or status.get("message")
    message = wire_to_message(msg_data) if msg_data else None
    artifacts = [wire_to_artifact(a) for a in (data.get("artifacts") or [])]
    metadata = dict(data.get("metadata") or {})
    session_id = data.get("sessionId")
    if session_id:
        metadata["session_id"] = session_id
    return A2ATask(
        task_id=str(data.get("id") or data.get("task_id") or new_id("task")),
        state=state,
        message=message,
        artifacts=artifacts,
        metadata=metadata,
    )


def task_status_to_wire(status: A2ATaskStatus) -> dict[str, Any]:
    """A2ATaskStatus → wire Task dict (subset: id + status block)."""
    msg_wire = message_to_wire(status.message) if status.message else None
    return {
        "id": status.task_id,
        "status": {
            "state": WIRE_STATE_MAP.get(status.state, "submitted"),
            "timestamp": status.updated_at.isoformat() if status.updated_at else None,
            "message": msg_wire,
        },
        "artifacts": [artifact_to_wire(a) for a in status.artifacts],
    }


# ---------------------------------------------------------------------
# JSON-RPC 2.0 envelope helpers
# ---------------------------------------------------------------------


def make_jsonrpc_request(
    method: str,
    params: dict[str, Any],
    request_id: str | int | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 request envelope."""
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": request_id,
    }


def make_jsonrpc_response(
    result: Any | None = None,
    error: dict[str, Any] | None = None,
    request_id: str | int | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 response envelope.

    Exactly one of ``result`` or ``error`` is included (per spec, a
    response carries one or the other, never both).
    """
    resp: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    return resp


__all__ = [
    "WIRE_STATE_MAP",
    "WIRE_STATE_REVERSE",
    "agent_card_to_wire",
    "wire_to_agent_card",
    "message_to_wire",
    "wire_to_message",
    "artifact_to_wire",
    "wire_to_artifact",
    "task_to_wire",
    "wire_to_task",
    "task_status_to_wire",
    "make_jsonrpc_request",
    "make_jsonrpc_response",
]
