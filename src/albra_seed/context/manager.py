"""RunContext manager.

Creates, looks up, and tracks `RunContext` instances by `execution_id`.
Supports parent/child execution chains (an execution can spawn child
executions which carry `parent_execution_id`). All contexts are kept in
memory until explicitly removed.

The manager also bridges to `CorrelationTracker` so that creating a
context optionally activates a `CorrelationScope` — every call into
the runtime, providers, audit, memory, and tools can then read the
active correlation_id from the tracker.
"""

from __future__ import annotations

from typing import Any

import structlog

from albra_seed.context.correlation import CorrelationScope, CorrelationTracker
from albra_seed.core.types import (
    AutonomyLevel,
    RiskLevel,
    RunContext,
    new_id,
    utc_now,
)

log = structlog.get_logger("albra.context.manager")


class ContextManager:
    """Owns the lifecycle of RunContext instances."""

    def __init__(self) -> None:
        self._contexts: dict[str, RunContext] = {}
        self._scopes: dict[str, CorrelationScope] = {}

    # ------------------------------------------------------------------ #
    # Creation / lookup                                                  #
    # ------------------------------------------------------------------ #

    def create(
        self,
        *,
        agent_id: str,
        task: str,
        correlation_id: str | None = None,
        parent_execution_id: str | None = None,
        session_id: str | None = None,
        autonomy_level: AutonomyLevel = AutonomyLevel.L0_OBSERVE,
        risk_level: RiskLevel = RiskLevel.UNKNOWN,
        permissions: list[str] | None = None,
        inputs: dict[str, Any] | None = None,
        activate_scope: bool = True,
    ) -> RunContext:
        """Create a fresh RunContext and (optionally) open a CorrelationScope.

        When `activate_scope=True`, the scope is held open and reset
        only when `close()` is called for this execution_id. This is the
        common case for an agent run.
        """
        execution_id = new_id("exec")
        correlation_id = correlation_id or new_id("corr")
        ctx = RunContext(
            execution_id=execution_id,
            correlation_id=correlation_id,
            parent_execution_id=parent_execution_id,
            agent_id=agent_id,
            session_id=session_id,
            task=task,
            autonomy_level=autonomy_level,
            risk_level=risk_level,
            permissions=list(permissions or []),
            inputs=dict(inputs or {}),
        )
        self._contexts[execution_id] = ctx
        if activate_scope:
            scope = CorrelationScope(
                correlation_id=correlation_id,
                execution_id=execution_id,
                parent_execution_id=parent_execution_id,
                agent_id=agent_id,
            )
            scope.__enter__()
            self._scopes[execution_id] = scope
        log.info(
            "context.created",
            execution_id=execution_id,
            correlation_id=correlation_id,
            parent=parent_execution_id,
            agent_id=agent_id,
        )
        return ctx

    def get(self, execution_id: str) -> RunContext | None:
        return self._contexts.get(execution_id)

    def require(self, execution_id: str) -> RunContext:
        ctx = self.get(execution_id)
        if ctx is None:
            raise KeyError(f"context not found: {execution_id}")
        return ctx

    def list_active(self) -> list[str]:
        return [c.execution_id for c in self._contexts.values() if c.finished_at is None]

    # ------------------------------------------------------------------ #
    # Parent/child relationships                                         #
    # ------------------------------------------------------------------ #

    def create_child(
        self,
        parent_execution_id: str,
        *,
        agent_id: str,
        task: str,
        autonomy_level: AutonomyLevel = AutonomyLevel.L0_OBSERVE,
        risk_level: RiskLevel = RiskLevel.UNKNOWN,
        inputs: dict[str, Any] | None = None,
    ) -> RunContext:
        """Create a child context. Propagates correlation_id from parent."""
        parent = self.require(parent_execution_id)
        return self.create(
            agent_id=agent_id,
            task=task,
            correlation_id=parent.correlation_id,  # propagate
            parent_execution_id=parent_execution_id,
            session_id=parent.session_id,
            autonomy_level=autonomy_level,
            risk_level=risk_level,
            inputs=inputs,
        )

    # ------------------------------------------------------------------ #
    # Closing / mutation                                                 #
    # ------------------------------------------------------------------ #

    def close(self, execution_id: str) -> RunContext | None:
        ctx = self._contexts.get(execution_id)
        if ctx is None:
            return None
        ctx.finished_at = utc_now()
        scope = self._scopes.pop(execution_id, None)
        if scope is not None:
            scope.__exit__(None, None, None)
        log.info("context.closed", execution_id=execution_id)
        return ctx

    def remove(self, execution_id: str) -> bool:
        """Drop a context from the registry."""
        self.close(execution_id)
        return self._contexts.pop(execution_id, None) is not None

    # ------------------------------------------------------------------ #
    # Correlation bridge                                                 #
    # ------------------------------------------------------------------ #

    def current(self) -> dict[str, Any]:
        """Return the active correlation ids from the tracker."""
        return CorrelationTracker.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "active_count": len(self._contexts),
            "active_execution_ids": self.list_active(),
            "scopes_open": len(self._scopes),
        }


__all__ = ["ContextManager"]
