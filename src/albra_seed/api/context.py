"""Shared context object passed to every API route handler.

`RequestContext` keeps the Runtime + ConfigManager references in one
place so route handlers can be pure functions. It also exposes helpers
to look up a registered agent and redact secrets before responding.
"""

from __future__ import annotations

from typing import Any

from albra_seed.config import ConfigManager
from albra_seed.config.secrets import redact_secret_values
from albra_seed.core.identity import AgentManifest
from albra_seed.runtime.runtime import Runtime


class RequestContext:
    """Bundles Runtime + ConfigManager + optional agent registry."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        config: ConfigManager | None = None,
        agents: dict[str, Any] | None = None,
        a2a_server: Any = None,
    ) -> None:
        self.runtime = runtime
        self.config: ConfigManager = config or ConfigManager(seed=runtime.config)
        # Map of agent_id -> Agent instance (registered by callers).
        self.agents: dict[str, Any] = agents or {}
        # Optional A2AServer — when provided, the FastAPI app mounts the
        # A2A JSON-RPC + well-known routes. None = CORE_ONLY (no A2A HTTP).
        self.a2a_server: Any = a2a_server
        # Last execution trace by execution_id (in-memory; not durable).
        self._traces: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # Agent registry                                                      #
    # ------------------------------------------------------------------ #

    def register_agent(self, agent: Any) -> None:
        self.agents[agent.identity.agent_id] = agent

    def default_agent(self) -> Any | None:
        if not self.agents:
            return None
        return next(iter(self.agents.values()))

    def get_agent(self, agent_id: str | None = None) -> Any | None:
        if agent_id is None:
            return self.default_agent()
        return self.agents.get(agent_id)

    # ------------------------------------------------------------------ #
    # Trace cache                                                         #
    # ------------------------------------------------------------------ #

    def store_trace(self, execution_id: str, trace: dict[str, Any]) -> None:
        self._traces[execution_id] = trace

    def get_trace(self, execution_id: str) -> dict[str, Any] | None:
        return self._traces.get(execution_id)

    # ------------------------------------------------------------------ #
    # Manifest + redaction helpers                                        #
    # ------------------------------------------------------------------ #

    def agent_manifest(self, agent_id: str | None = None) -> AgentManifest | None:
        agent = self.get_agent(agent_id)
        if agent is None:
            return None
        return agent.identity.to_manifest()

    def redacted(self, payload: Any) -> Any:
        """Run any payload through the secrets redactor."""
        return redact_secret_values(payload)


__all__ = ["RequestContext"]
