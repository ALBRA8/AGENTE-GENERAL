"""A2ARegistry — directory of known A2A agents.

Records each agent's `endpoint`, optional `AgentCard`, availability and
last-seen timestamp. Used by clients to resolve an agent_id → endpoint
before issuing a task.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.a2a.card import AgentCard
from albra_seed.core.types import utc_now


class A2AAgentRecord(BaseModel):
    """A registered A2A agent entry."""

    agent_id: str
    name: str = ""
    endpoint: str
    card: AgentCard | None = None
    available: bool = True
    last_seen: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2ARegistry:
    """In-memory registry of known A2A agents.

    Mirrors the existing `AgentRegistry` shape so callers can swap them
    without learning a new API.
    """

    def __init__(self) -> None:
        self._agents: dict[str, A2AAgentRecord] = {}

    def register(self, record: A2AAgentRecord) -> None:
        if not record.agent_id:
            raise ValueError("agent_id is required")
        if not record.endpoint:
            raise ValueError("endpoint is required")
        self._agents[record.agent_id] = record

    def unregister(self, agent_id: str) -> bool:
        return self._agents.pop(agent_id, None) is not None

    def get(self, agent_id: str) -> A2AAgentRecord | None:
        return self._agents.get(agent_id)

    def get_by_endpoint(self, endpoint: str) -> A2AAgentRecord | None:
        for rec in self._agents.values():
            if rec.endpoint == endpoint:
                return rec
        return None

    def list(self) -> list[A2AAgentRecord]:
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

    def touch(self, agent_id: str) -> bool:
        """Update the last_seen timestamp for an agent."""
        rec = self._agents.get(agent_id)
        if rec is None:
            return False
        rec.last_seen = utc_now()
        return True


__all__ = ["A2ARegistry", "A2AAgentRecord"]
