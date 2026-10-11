"""Agent registry — registers known ALBRA agents by `agent_id`.

Used by the HandoffManager to route a handoff to the correct agent
without coupling the manager to any specific transport or runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now

# A handler is an async callable (HandoffRequest -> HandoffResponse).
HandoffHandler = Callable[["HandoffRequest"], Awaitable["HandoffResponse"]]


class AgentRecord(BaseModel):
    """A registered ALBRA agent entry."""

    agent_id: str
    name: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    handler: Any | None = None  # HandoffHandler — kept untyped for pickling
    metadata: dict[str, Any] = Field(default_factory=dict)
    registered_at: datetime = Field(default_factory=utc_now)
    available: bool = True


class AgentRegistry:
    """In-memory registry of known ALBRA agents.

    The registry never invokes handlers itself — it only records the
    callable so the HandoffManager can resolve `target` -> handler. This
    keeps the registry pure-data and unit-testable.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentRecord] = {}

    def register(self, record: AgentRecord) -> None:
        if not record.agent_id:
            raise ValueError("agent_id is required")
        self._agents[record.agent_id] = record

    def unregister(self, agent_id: str) -> bool:
        return self._agents.pop(agent_id, None) is not None

    def get(self, agent_id: str) -> AgentRecord | None:
        return self._agents.get(agent_id)

    def list(self) -> list[AgentRecord]:
        return list(self._agents.values())

    def is_available(self, agent_id: str) -> bool:
        rec = self._agents.get(agent_id)
        return rec is not None and rec.available

    def set_available(self, agent_id: str, available: bool) -> bool:
        rec = self._agents.get(agent_id)
        if rec is None:
            return False
        rec.available = available
        return True

    def resolve_handler(self, agent_id: str) -> HandoffHandler | None:
        rec = self._agents.get(agent_id)
        if rec is None or not rec.available:
            return None
        return rec.handler


__all__ = ["AgentRegistry", "AgentRecord", "HandoffHandler"]
