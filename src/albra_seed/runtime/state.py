"""Mutable per-agent runtime state.

The AgentState is a lightweight scratchpad for the *current* execution
context of an Agent instance. It is NOT the source of truth for lifecycle
(that's `LifecycleManager`) nor for the RunContext (that's the RunContext
itself, which is fully reconstructable). AgentState exists to give the
Runtime, the Doctor, and observers a fast read on "what is this agent
doing right now?".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import AgentResponse, RunContext, utc_now
from albra_seed.runtime.lifecycle import LifecyclePhase


class AgentState(BaseModel):
    """Live state of a single Agent instance.

    Fields are intentionally coarse-grained; detailed traces live on the
    RunContext and on the AgentLoop's event log.
    """

    agent_id: str
    phase: LifecyclePhase = LifecyclePhase.UNINITIALIZED
    current_execution_id: str | None = None
    current_context: RunContext | None = None
    last_error: str | None = None
    last_response: AgentResponse | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    # -- mutators --------------------------------------------------------

    def begin_execution(self, context: RunContext) -> None:
        """Mark the start of a new execution cycle."""
        self.current_execution_id = context.execution_id
        self.current_context = context
        self.phase = LifecyclePhase.RUNNING
        self.last_error = None
        self.metrics["executions"] = self.metrics.get("executions", 0) + 1
        self._touch()

    def finish_execution(self, response: AgentResponse | None = None) -> None:
        """Mark the successful end of an execution cycle."""
        self.phase = LifecyclePhase.READY
        if response is not None:
            self.last_response = response
        self.metrics["succeeded"] = self.metrics.get("succeeded", 0) + 1
        self._touch()

    def fail_execution(self, error: str) -> None:
        """Mark the failed end of an execution cycle."""
        self.phase = LifecyclePhase.ERROR
        self.last_error = error
        self.metrics["failed"] = self.metrics.get("failed", 0) + 1
        self._touch()

    def reset(self) -> None:
        """Clear transient execution state (keeps metrics)."""
        self.phase = LifecyclePhase.READY
        self.current_execution_id = None
        self.current_context = None
        self.last_error = None
        self._touch()

    # -- accessors -------------------------------------------------------

    def is_busy(self) -> bool:
        return self.phase == LifecyclePhase.RUNNING

    def snapshot(self) -> dict[str, Any]:
        """A JSON-serializable snapshot suitable for /health and /status."""
        return {
            "agent_id": self.agent_id,
            "phase": self.phase.value,
            "current_execution_id": self.current_execution_id,
            "last_error": self.last_error,
            "metrics": dict(self.metrics),
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    # -- internal --------------------------------------------------------

    def _touch(self) -> None:
        self.updated_at = utc_now()


__all__ = ["AgentState"]
