"""A2AClient — sends A2A tasks to remote agents via a transport.

The client is transport-agnostic: pass it an `InProcessTransport` for
CORE_ONLY mode or an `HTTPTransport` for network mode. The same client
API works either way.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from albra_seed.a2a.contract import (
    A2AMessage,
    A2ATask,
    A2ATaskStatus,
    TaskState,
)
from albra_seed.a2a.transport import A2ATransport
from albra_seed.core.types import new_id, utc_now


class A2AClient:
    """A2A client — sends tasks to remote agents."""

    def __init__(
        self,
        transport: A2ATransport,
        *,
        token: str | None = None,
        requester: str = "a2a-client",
    ) -> None:
        self._transport = transport
        self._token = token
        self._requester = requester

    @property
    def transport(self) -> A2ATransport:
        return self._transport

    async def send_task(
        self,
        target_url: str,
        *,
        message: str | A2AMessage,
        deadline: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        target: str | None = None,
    ) -> A2ATask:
        """Send a single task to `target_url`. Returns the updated task."""
        msg = A2AMessage.from_text(message) if isinstance(message, str) else message
        task = A2ATask(
            task_id=new_id("task"),
            state=TaskState.SUBMITTED,
            message=msg,
            requester=self._requester,
            target=target or target_url,
            deadline=deadline,
            metadata=dict(metadata or {}),
        )
        return await self._transport.send(
            target_url, task, token=self._token
        )

    async def get_status(
        self,
        target_url: str,
        task_id: str,
        *,
        token: str | None = None,
    ) -> A2ATaskStatus | None:
        """Query the status of a previously-submitted task.

        ``token`` overrides the client-level default (``self._token``)
        when provided. The token travels in the ``Authorization`` header
        over HTTP; in-process it is passed directly to the server's
        security filter.
        """
        effective_token = token if token is not None else self._token
        return await self._transport.get_status(
            target_url, task_id, token=effective_token
        )

    async def cancel(
        self,
        target_url: str,
        task_id: str,
        *,
        token: str | None = None,
    ) -> A2ATask | None:
        """Cancel a previously-submitted task.

        ``token`` overrides the client-level default (``self._token``)
        when provided.
        """
        effective_token = token if token is not None else self._token
        return await self._transport.cancel(
            target_url, task_id, token=effective_token
        )

    async def send_with_timeout(
        self,
        target_url: str,
        *,
        message: str | A2AMessage,
        timeout_seconds: float = 30.0,
        metadata: dict[str, Any] | None = None,
        target: str | None = None,
    ) -> A2ATask:
        """Convenience wrapper: send with a relative timeout."""
        deadline = utc_now() + timedelta(seconds=timeout_seconds)
        return await self.send_task(
            target_url,
            message=message,
            deadline=deadline,
            metadata=metadata,
            target=target,
        )


__all__ = ["A2AClient"]
