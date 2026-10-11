"""A2A protocol contract models.

These mirror the public A2A protocol schema (Task, Message, Artifact,
TaskStatus) so ALBRA agents can interoperate with other A2A-compliant
agents without leaking ALBRA-internal types (HandoffRequest, etc.) over
the wire.

Mapping (handoff ↔ a2a):
  HandoffRequest.execution_id      ↔ A2ATask.task_id
  HandoffRequest.requester          ↔ A2ATask.requester
  HandoffRequest.target             ↔ A2ATask.target
  HandoffRequest.objective          ↔ A2ATask.message.parts[0].text
  HandoffRequest.correlation_id    ↔ A2ATask.correlation_id
  HandoffRequest.parent_execution_id ↔ A2ATask.parent_execution_id
  HandoffRequest.deadline           ↔ A2ATask.deadline
  HandoffResponse.status            ↔ A2ATask.state (mapped via _map_state)
  HandoffResponse.result            ↔ A2AArtifact.parts
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now


class TaskState(str, Enum):
    """A2A task lifecycle states (per A2A protocol)."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input_required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


# Terminal states per the A2A spec.
_TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {TaskState.COMPLETED, TaskState.CANCELED, TaskState.FAILED}
)


class A2AMessage(BaseModel):
    """An A2A message: role + list of typed parts.

    `parts` follows the A2A Part schema: each part is a dict with at
    least `type` (e.g. "text", "file", "data") and a value field whose
    key depends on the type ("text" for text parts, etc.).
    """

    role: str = "user"
    parts: list[dict[str, Any]] = Field(default_factory=list)
    message_id: str = Field(default_factory=lambda: new_id("msg"))
    task_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)

    @classmethod
    def from_text(
        cls, text: str, *, role: str = "user", task_id: str | None = None
    ) -> A2AMessage:
        """Convenience constructor for a single text part."""
        return cls(role=role, parts=[{"type": "text", "text": text}], task_id=task_id)

    @property
    def text(self) -> str:
        """Concatenated text of all text-parts. Empty if none."""
        chunks: list[str] = []
        for p in self.parts:
            if p.get("type") == "text" and "text" in p:
                chunks.append(str(p["text"]))
        return "".join(chunks)


class A2AArtifact(BaseModel):
    """An A2A artifact — a structured output produced by a task."""

    name: str
    description: str = ""
    parts: list[dict[str, Any]] = Field(default_factory=list)
    artifact_id: str = Field(default_factory=lambda: new_id("art"))
    timestamp: datetime = Field(default_factory=utc_now)

    @classmethod
    def from_text(
        cls, text: str, *, name: str = "result", description: str = ""
    ) -> A2AArtifact:
        """Convenience constructor for a single text part."""
        return cls(
            name=name,
            description=description,
            parts=[{"type": "text", "text": text}],
        )


class A2ATask(BaseModel):
    """An A2A task — the unit of work exchanged between agents.

    `task_id` is the public identifier that travels over the wire; it is
    reused as the underlying `HandoffRequest.execution_id` so the entire
    provenance + audit + evidence chain stays intact across both layers.
    """

    task_id: str = Field(default_factory=lambda: new_id("task"))
    state: TaskState = TaskState.SUBMITTED
    message: A2AMessage | None = None
    artifacts: list[A2AArtifact] = Field(default_factory=list)
    requester: str = "anonymous"
    target: str = "anonymous"
    correlation_id: str | None = None
    parent_execution_id: str | None = None
    deadline: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """True when the task has reached a final state."""
        return self.state in _TERMINAL_STATES

    @property
    def objective(self) -> str:
        """Convenience: the task's textual objective (message text)."""
        return self.message.text if self.message else ""


class A2ATaskStatus(BaseModel):
    """Status snapshot returned by `get_status`."""

    task_id: str
    state: TaskState
    message: A2AMessage | None = None
    artifacts: list[A2AArtifact] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


__all__ = [
    "TaskState",
    "A2AMessage",
    "A2AArtifact",
    "A2ATask",
    "A2ATaskStatus",
]
