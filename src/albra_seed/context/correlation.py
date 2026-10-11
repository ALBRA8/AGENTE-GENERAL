"""Correlation tracker — propagates `correlation_id` and `execution_id`
across all subsystems (memory, audit, tools, providers).

Implemented with `contextvars.ContextVar` so the values are correctly
scoped to async tasks. Subsystems call `CorrelationTracker.current()`
to retrieve the active ids; if none are set, the tracker falls back to
generating fresh ones (rather than returning None) so logs are never
missing correlation.
"""

from __future__ import annotations

import contextvars
from typing import Any

from albra_seed.core.types import new_id, utc_now

# Context vars are the only safe way to propagate request-scoped state
# across `await` boundaries without passing it explicitly through every
# function signature. They are local to each asyncio.Task.
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "albra_correlation_id", default=None
)
_execution_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "albra_execution_id", default=None
)
_parent_execution_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "albra_parent_execution_id", default=None
)
_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "albra_agent_id", default=None
)


class CorrelationScope:
    """Context manager / async context manager that sets the correlation
    ids for the duration of a block, then restores the previous values.
    """

    def __init__(
        self,
        *,
        correlation_id: str | None = None,
        execution_id: str | None = None,
        parent_execution_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._correlation_id = correlation_id or new_id("corr")
        self._execution_id = execution_id or new_id("exec")
        self._parent_execution_id = parent_execution_id
        self._agent_id = agent_id
        self._tokens: list[tuple[contextvars.ContextVar[Any], Any]] = []
        self._started_at: Any = None

    def __enter__(self) -> "CorrelationScope":
        self._started_at = utc_now()
        self._tokens.append(
            (_correlation_id, _correlation_id.set(self._correlation_id))
        )
        self._tokens.append(
            (_execution_id, _execution_id.set(self._execution_id))
        )
        if self._parent_execution_id is not None:
            self._tokens.append(
                (
                    _parent_execution_id,
                    _parent_execution_id.set(self._parent_execution_id),
                )
            )
        if self._agent_id is not None:
            self._tokens.append((_agent_id, _agent_id.set(self._agent_id)))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Reset in reverse order.
        for var, token in reversed(self._tokens):
            var.reset(token)
        self._tokens.clear()

    async def __aenter__(self) -> "CorrelationScope":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.__exit__(exc_type, exc, tb)

    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    @property
    def execution_id(self) -> str:
        return self._execution_id


class CorrelationTracker:
    """Static accessor for the current correlation context.

    All methods are classmethods — the tracker itself is stateless; the
    state lives in contextvars.
    """

    @classmethod
    def current_correlation_id(cls) -> str:
        """Return the active correlation_id or generate one on the fly."""
        cid = _correlation_id.get()
        if cid is None:
            cid = new_id("corr")
            _correlation_id.set(cid)
        return cid

    @classmethod
    def current_execution_id(cls) -> str | None:
        return _execution_id.get()

    @classmethod
    def current_parent_execution_id(cls) -> str | None:
        return _parent_execution_id.get()

    @classmethod
    def current_agent_id(cls) -> str | None:
        return _agent_id.get()

    @classmethod
    def snapshot(cls) -> dict[str, Any]:
        """Return all currently-set correlation ids."""
        return {
            "correlation_id": cls.current_correlation_id(),
            "execution_id": cls.current_execution_id(),
            "parent_execution_id": cls.current_parent_execution_id(),
            "agent_id": cls.current_agent_id(),
        }

    @classmethod
    def scope(
        cls,
        *,
        correlation_id: str | None = None,
        execution_id: str | None = None,
        parent_execution_id: str | None = None,
        agent_id: str | None = None,
    ) -> CorrelationScope:
        """Open a correlation scope."""
        return CorrelationScope(
            correlation_id=correlation_id,
            execution_id=execution_id,
            parent_execution_id=parent_execution_id,
            agent_id=agent_id,
        )


__all__ = ["CorrelationTracker", "CorrelationScope"]
