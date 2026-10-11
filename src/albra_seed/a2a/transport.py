"""A2A transport layer.

Transports move A2ATask objects between an `A2AClient` and an
`A2AServer`. Two implementations are provided:

  * `InProcessTransport` — always available, routes calls directly to
    a registered `A2AServer` via a dict lookup. Used in CORE_ONLY mode
    and in unit tests.

  * `HTTPTransport` — lazy-imports `httpx` in `__init__`. Raises
    `NotImplementedError` if `httpx` is not installed. This keeps the
    seed CORE_ONLY (no hard dependency on httpx).

Both implementations expose the same async API so callers can swap
them without touching the A2AClient.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from albra_seed.a2a.contract import A2ATask, A2ATaskStatus, TaskState


class A2ATransport(ABC):
    """Abstract A2A transport.

    All methods are async; sync callers should use `asyncio.run`.
    """

    @abstractmethod
    async def send(
        self,
        target_url: str,
        task: A2ATask,
        *,
        token: str | None = None,
    ) -> A2ATask:
        """Send a task to a remote agent at `target_url`. Returns the
        updated task (with state set by the receiver)."""
        raise NotImplementedError

    @abstractmethod
    async def get_status(
        self, target_url: str, task_id: str
    ) -> A2ATaskStatus | None:
        """Query the status of a previously-submitted task."""
        raise NotImplementedError

    @abstractmethod
    async def cancel(
        self, target_url: str, task_id: str
    ) -> A2ATask | None:
        """Cancel a previously-submitted task. Returns the final task
        state (CANCELED) or None if not found."""
        raise NotImplementedError

    async def health(self, target_url: str) -> bool:  # pragma: no cover
        """Optional health probe. Default: True for in-process, False
        for transports that cannot reach the endpoint."""
        return True


# ---------------------------------------------------------------------
# In-process transport
# ---------------------------------------------------------------------


class InProcessTransport(A2ATransport):
    """Routes A2A calls directly to a registered in-process server.

    ALWAYS available — no external dependencies. Suitable for
    CORE_ONLY mode, tests, and single-process multi-agent topologies.
    """

    def __init__(self) -> None:
        self._servers: dict[str, Any] = {}

    def register(self, endpoint: str, server: Any) -> None:
        """Register an A2AServer under `endpoint`."""
        self._servers[endpoint] = server

    def unregister(self, endpoint: str) -> bool:
        return self._servers.pop(endpoint, None) is not None

    def has_endpoint(self, endpoint: str) -> bool:
        return endpoint in self._servers

    async def send(
        self,
        target_url: str,
        task: A2ATask,
        *,
        token: str | None = None,
    ) -> A2ATask:
        server = self._servers.get(target_url)
        if server is None:
            # Mimic HTTP behaviour: a non-existent endpoint yields a
            # FAILED task with a transport-level error in metadata.
            task.state = TaskState.FAILED
            task.metadata["transport_error"] = (
                f"unknown endpoint: {target_url}"
            )
            return task
        return await server.receive(task, token=token)

    async def get_status(
        self, target_url: str, task_id: str
    ) -> A2ATaskStatus | None:
        server = self._servers.get(target_url)
        if server is None:
            return None
        return await server.get_status(task_id)

    async def cancel(
        self, target_url: str, task_id: str
    ) -> A2ATask | None:
        server = self._servers.get(target_url)
        if server is None:
            return None
        return await server.cancel(task_id)

    async def health(self, target_url: str) -> bool:
        return target_url in self._servers


# ---------------------------------------------------------------------
# HTTP transport (lazy httpx import)
# ---------------------------------------------------------------------


class HTTPTransport(A2ATransport):
    """HTTP-based A2A transport.

    The `httpx` package is imported LAZILY in `__init__` so the seed
    remains CORE_ONLY — `httpx` is an OPTIONAL dependency. If it is
    not installed, instantiating `HTTPTransport` raises
    `NotImplementedError`.

    All send/get_status/cancel operations issue JSON requests to the
    target endpoint and parse JSON responses back into A2A models.
    """

    def __init__(self, *, timeout: float = 30.0) -> None:
        try:
            import httpx  # noqa: F401  (we just need to verify import)
        except ImportError as exc:
            raise NotImplementedError(
                "HTTPTransport requires the optional 'httpx' "
                "package. Install it via `pip install httpx` or use "
                "InProcessTransport for CORE_ONLY mode."
            ) from exc
        self._timeout = timeout
        # Keep a reference to httpx for use in the async methods.
        import httpx

        self._httpx = httpx

    async def send(
        self,
        target_url: str,
        task: A2ATask,
        *,
        token: str | None = None,
    ) -> A2ATask:
        raise NotImplementedError(
            "HTTPTransport.send is not yet implemented; "
            "use InProcessTransport for in-process A2A."
        )

    async def get_status(
        self, target_url: str, task_id: str
    ) -> A2ATaskStatus | None:
        raise NotImplementedError(
            "HTTPTransport.get_status is not yet implemented; "
            "use InProcessTransport for in-process A2A."
        )

    async def cancel(
        self, target_url: str, task_id: str
    ) -> A2ATask | None:
        raise NotImplementedError(
            "HTTPTransport.cancel is not yet implemented; "
            "use InProcessTransport for in-process A2A."
        )


__all__ = ["A2ATransport", "InProcessTransport", "HTTPTransport"]
