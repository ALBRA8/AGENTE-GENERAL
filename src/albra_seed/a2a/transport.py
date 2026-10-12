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

from albra_seed.a2a.card import AgentCard
from albra_seed.a2a.contract import A2ATask, A2ATaskStatus, TaskState
from albra_seed.core.types import utc_now


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
        self,
        target_url: str,
        task_id: str,
        *,
        token: str | None = None,
    ) -> A2ATaskStatus | None:
        """Query the status of a previously-submitted task.

        ``token`` is forwarded to the receiving server's security filter
        (in-process) or sent in the ``Authorization`` header (HTTP).
        """
        raise NotImplementedError

    @abstractmethod
    async def cancel(
        self,
        target_url: str,
        task_id: str,
        *,
        token: str | None = None,
    ) -> A2ATask | None:
        """Cancel a previously-submitted task. Returns the final task
        state (CANCELED) or None if not found.

        ``token`` is forwarded to the receiving server's security filter
        (in-process) or sent in the ``Authorization`` header (HTTP).
        """
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
        self,
        target_url: str,
        task_id: str,
        *,
        token: str | None = None,
    ) -> A2ATaskStatus | None:
        server = self._servers.get(target_url)
        if server is None:
            return None
        return await server.get_status(task_id, token=token)

    async def cancel(
        self,
        target_url: str,
        task_id: str,
        *,
        token: str | None = None,
    ) -> A2ATask | None:
        server = self._servers.get(target_url)
        if server is None:
            return None
        return await server.cancel(task_id, token=token)

    async def health(self, target_url: str) -> bool:
        return target_url in self._servers


# ---------------------------------------------------------------------
# HTTP transport (lazy httpx import)
# ---------------------------------------------------------------------


class HTTPTransport(A2ATransport):
    """HTTP-based A2A transport implementing the A2A 1.0 JSON-RPC wire format.

    The ``httpx`` package is imported LAZILY in ``__init__`` so the seed
    remains CORE_ONLY — ``httpx`` is an OPTIONAL dependency. If it is
    not installed, instantiating ``HTTPTransport`` raises
    ``NotImplementedError`` (preserving the CORE_ONLY birth contract).

    All send/get_status/cancel operations issue JSON-RPC 2.0 requests to
    the target endpoint and parse JSON-RPC responses back into A2A
    models. The ``discover`` method fetches the well-known agent card.

    The optional ``transport`` parameter accepts an ``httpx.MockTransport``
    (or any ``httpx.BaseTransport``) so unit tests can exercise the full
    HTTP path without a live network.
    """

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        transport: Any = None,
    ) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError as exc:
            raise NotImplementedError(
                "HTTPTransport requires the optional 'httpx' "
                "package. Install it via `pip install httpx` or use "
                "InProcessTransport for CORE_ONLY mode."
            ) from exc
        self._httpx = httpx
        self._timeout = timeout
        if transport is not None:
            self._client = httpx.AsyncClient(transport=transport, timeout=timeout)
        else:
            self._client = httpx.AsyncClient(timeout=timeout)

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    def _headers(self, token: str | None) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    async def _post_jsonrpc(
        self,
        target_url: str,
        rpc: dict[str, Any],
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        """POST a JSON-RPC envelope and return the parsed response dict."""
        resp = await self._client.post(
            target_url, json=rpc, headers=self._headers(token)
        )
        resp.raise_for_status()
        return resp.json()

    # -----------------------------------------------------------------
    # A2ATransport implementation
    # -----------------------------------------------------------------

    async def send(
        self,
        target_url: str,
        task: A2ATask,
        *,
        token: str | None = None,
    ) -> A2ATask:
        """POST JSON-RPC ``message/send`` to ``target_url``.

        On a JSON-RPC error response, returns the original task marked
        FAILED with the error payload stored under
        ``task.metadata["error"]``.
        """
        from albra_seed.a2a.wire import (
            make_jsonrpc_request,
            message_to_wire,
            wire_to_task,
        )

        wire_msg = message_to_wire(task.message) if task.message else {}
        rpc = make_jsonrpc_request(
            "message/send",
            params={"message": wire_msg, "metadata": dict(task.metadata or {})},
            request_id=task.task_id,
        )
        rpc_resp = await self._post_jsonrpc(target_url, rpc, token=token)
        if "error" in rpc_resp:
            return task.model_copy(update={
                "state": TaskState.FAILED,
                "metadata": {**dict(task.metadata or {}), "error": rpc_resp["error"]},
            })
        return wire_to_task(rpc_resp.get("result") or {})

    async def get_status(
        self,
        target_url: str,
        task_id: str,
        *,
        token: str | None = None,
    ) -> A2ATaskStatus | None:
        """POST JSON-RPC ``tasks/get`` and return an A2ATaskStatus."""
        from albra_seed.a2a.wire import (
            WIRE_STATE_REVERSE,
            make_jsonrpc_request,
            wire_to_artifact,
            wire_to_message,
        )

        rpc = make_jsonrpc_request(
            "tasks/get",
            params={"id": task_id},
            request_id=task_id,
        )
        rpc_resp = await self._post_jsonrpc(target_url, rpc, token=token)
        if "error" in rpc_resp:
            return None
        wire_task = rpc_resp.get("result") or {}
        if not wire_task:
            return None
        status_block = wire_task.get("status") or {}
        state_str = status_block.get("state", "submitted")
        state = WIRE_STATE_REVERSE.get(state_str, TaskState.SUBMITTED)
        msg_data = status_block.get("message") or wire_task.get("message")
        message = wire_to_message(msg_data) if msg_data else None
        artifacts = [wire_to_artifact(a) for a in (wire_task.get("artifacts") or [])]
        return A2ATaskStatus(
            task_id=str(wire_task.get("id") or task_id),
            state=state,
            message=message,
            artifacts=artifacts,
            updated_at=utc_now(),
        )

    async def cancel(
        self,
        target_url: str,
        task_id: str,
        *,
        token: str | None = None,
    ) -> A2ATask | None:
        """POST JSON-RPC ``tasks/cancel`` and return the cancelled task."""
        from albra_seed.a2a.wire import make_jsonrpc_request, wire_to_task

        rpc = make_jsonrpc_request(
            "tasks/cancel",
            params={"id": task_id},
            request_id=task_id,
        )
        rpc_resp = await self._post_jsonrpc(target_url, rpc, token=token)
        if "error" in rpc_resp:
            return None
        wire_task = rpc_resp.get("result") or {}
        if not wire_task:
            return None
        return wire_to_task(wire_task)

    # -----------------------------------------------------------------
    # Discovery (not part of A2ATransport ABC — HTTP-specific extension)
    # -----------------------------------------------------------------

    async def discover(self, target_url: str) -> AgentCard | None:
        """GET ``{target_url}/.well-known/agent-card.json`` → AgentCard."""
        from albra_seed.a2a.wire import wire_to_agent_card

        url = f"{target_url.rstrip('/')}/.well-known/agent-card.json"
        try:
            resp = await self._client.get(url)
        except Exception:  # noqa: BLE001
            return None
        if resp.status_code != 200:
            return None
        try:
            return wire_to_agent_card(resp.json())
        except Exception:  # noqa: BLE001
            return None

    async def health(self, target_url: str) -> bool:  # noqa: ARG002
        """Verify the httpx client is alive (not closed)."""
        return not self._client.is_closed

    async def close(self) -> None:
        """Close the underlying httpx async client."""
        await self._client.aclose()


__all__ = ["A2ATransport", "InProcessTransport", "HTTPTransport"]
