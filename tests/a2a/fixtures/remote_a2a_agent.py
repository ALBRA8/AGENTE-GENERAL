"""Standalone A2A 1.0 reference agent for interoperability testing.

This fixture speaks raw A2A 1.0 JSON-RPC over HTTP — it does NOT use
ALBRA's A2A client/transport. It validates that ALBRA's HTTPTransport
+ A2AClient can interoperate with a generic A2A 1.0-compliant agent.

Design rules (per A2A-GAP-B contract):

  * NO domain knowledge — the agent only knows how to echo inputs and
    prefix responses with ``remote:``. This is the smallest reference
    surface that exercises the wire format end-to-end.
  * NO ALBRA imports on the agent side — only FastAPI / stdlib.
  * The agent stores tasks in an in-process dict so the test can verify
    state transitions across calls.
  * The agent supports the three JSON-RPC methods mandated by A2A 1.0:
    ``message/send``, ``tasks/get``, ``tasks/cancel``.
  * Discovery is exposed at ``GET /.well-known/agent-card.json`` so
    ALBRA's ``HTTPTransport.discover()`` can fetch the wire-format card.

The agent's JSON-RPC endpoint is mounted at ``POST /`` so the wire URL
passed to ALBRA's HTTPTransport is just ``http://remote-test/``. (ALBRA's
HTTPTransport posts the JSON-RPC envelope directly to the target URL.)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
import uuid

# NOTE: This module must NOT import anything from `albra_seed` — that
# would defeat the purpose of testing interop with a non-ALBRA agent.
# Only FastAPI / stdlib imports are allowed here.

try:
    from fastapi import FastAPI, Header, HTTPException, Request
except ImportError:  # pragma: no cover - fastapi is required at test time
    FastAPI = None  # type: ignore[assignment]
    Header = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]


def _now_iso() -> str:
    """UTC now in ISO 8601 string form."""
    return datetime.now(timezone.utc).isoformat()


def build_remote_a2a_agent_app(
    *,
    name: str = "remote-agent",
    endpoint: str = "http://remote-test",
) -> Any:
    """Build a minimal A2A 1.0 reference agent as a FastAPI app.

    Parameters
    ----------
    name : str
        Name advertised in the agent card.
    endpoint : str
        Endpoint advertised in the agent card's ``url`` field (returned
        verbatim to clients, not used for routing — the app's own routes
        decide what is served).

    Returns
    -------
    FastAPI
        A FastAPI app with three routes:
          * ``GET /.well-known/agent-card.json`` — discovery
          * ``POST /`` — JSON-RPC 2.0 dispatch
          * ``GET /__tasks`` — debug introspection (returns the task store)
    """
    if FastAPI is None:  # pragma: no cover - environment guard
        raise ImportError(
            "fastapi is required to build_remote_a2a_agent_app; "
            "install with `pip install fastapi`."
        )

    app = FastAPI(title=name)
    # In-process task store. Keyed by task_id (str) → wire task dict.
    tasks: dict[str, dict[str, Any]] = {}

    # -----------------------------------------------------------------
    # Discovery — A2A 1.0 well-known agent card.
    # -----------------------------------------------------------------
    @app.get("/.well-known/agent-card.json")
    async def get_card() -> dict[str, Any]:
        """Return the A2A 1.0 wire-format AgentCard for this agent."""
        return {
            "name": name,
            "description": "Reference A2A 1.0 agent for interop testing",
            "version": "1.0.0",
            "url": endpoint,
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "stateTransitionHistory": False,
            },
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [
                {
                    "id": "echo",
                    "name": "echo",
                    "description": "Echoes input prefixed with 'remote:'",
                    "tags": ["test"],
                    "inputModes": ["text"],
                    "outputModes": ["text"],
                }
            ],
            "securitySchemes": {},
            "security": [],
            "provider": {"organization": "TestFixture"},
        }

    # -----------------------------------------------------------------
    # JSON-RPC 2.0 dispatch.
    # -----------------------------------------------------------------
    @app.post("/")
    async def jsonrpc(
        request: Request,
        authorization: Optional[str] = Header(None),  # noqa: B008
    ) -> dict[str, Any]:
        """A2A 1.0 JSON-RPC dispatch.

        Methods implemented:
          * ``message/send`` — create a task, echo the input text
            prefixed with ``remote:`` as an artifact, mark COMPLETED.
          * ``tasks/get`` — return a status snapshot for a task.
          * ``tasks/cancel`` — mark the task CANCELED (terminal).

        Authorization header is accepted but NOT enforced — this is a
        reference fixture, not a security boundary. The header is echoed
        into the task metadata under ``remote_received_token`` so tests
        can verify propagation.
        """
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=f"invalid JSON body: {exc}",
            )

        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            raise HTTPException(
                status_code=400,
                detail="Not a JSON-RPC 2.0 request (missing jsonrpc='2.0')",
            )

        method = body.get("method")
        params = body.get("params") or {}
        req_id = body.get("id")

        # The Authorization header is captured (not enforced) so ALBRA
        # side tests can verify token propagation across HTTP.
        token = (
            authorization[len("Bearer ") :].strip()
            if authorization and authorization.startswith("Bearer ")
            else (authorization.strip() if authorization else None)
        )

        # -----------------------------------------------------------
        # message/send
        # -----------------------------------------------------------
        if method == "message/send":
            msg = params.get("message") or {}
            task_id = str(uuid.uuid4())
            parts = msg.get("parts") or []
            text = ""
            if parts and isinstance(parts, list) and isinstance(parts[0], dict):
                text = str(parts[0].get("text", ""))
            metadata = dict(params.get("metadata") or {})
            session_id = params.get("sessionId")
            if session_id:
                metadata["session_id"] = session_id
            if token:
                metadata["remote_received_token"] = True
            task: dict[str, Any] = {
                "id": task_id,
                "sessionId": metadata.pop("session_id", None),
                "status": {
                    "state": "completed",
                    "timestamp": _now_iso(),
                    "message": msg,
                },
                "message": msg,
                "artifacts": [
                    {
                        "name": "result",
                        "description": "echoed response from remote agent",
                        "parts": [{"type": "text", "text": f"remote: {text}"}],
                        "artifactId": f"art-{task_id}",
                    }
                ],
                "metadata": {**metadata, "remote_handled": True},
            }
            tasks[task_id] = task
            return {"jsonrpc": "2.0", "result": task, "id": req_id}

        # -----------------------------------------------------------
        # tasks/get
        # -----------------------------------------------------------
        if method == "tasks/get":
            task_id = params.get("id")
            stored = tasks.get(str(task_id)) if task_id is not None else None
            if stored is None:
                return {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32602,
                        "message": f"task not found: {task_id}",
                    },
                    "id": req_id,
                }
            return {
                "jsonrpc": "2.0",
                "result": {
                    "id": stored["id"],
                    "status": stored["status"],
                    "artifacts": stored.get("artifacts", []),
                },
                "id": req_id,
            }

        # -----------------------------------------------------------
        # tasks/cancel
        # -----------------------------------------------------------
        if method == "tasks/cancel":
            task_id = params.get("id")
            stored = tasks.get(str(task_id)) if task_id is not None else None
            if stored is None:
                return {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32602,
                        "message": f"task not found: {task_id}",
                    },
                    "id": req_id,
                }
            stored["status"] = {
                "state": "canceled",
                "timestamp": _now_iso(),
                "message": stored.get("message"),
            }
            return {"jsonrpc": "2.0", "result": stored, "id": req_id}

        # -----------------------------------------------------------
        # Unknown method
        # -----------------------------------------------------------
        return {
            "jsonrpc": "2.0",
            "error": {
                "code": -32601,
                "message": f"method not found: {method}",
            },
            "id": req_id,
        }

    # -----------------------------------------------------------------
    # Debug introspection — NOT part of A2A spec, used by tests only.
    # -----------------------------------------------------------------
    @app.get("/__tasks")
    async def list_tasks() -> dict[str, Any]:
        """Return the raw task store. Tests use this to verify state."""
        return {"tasks": list(tasks.values())}

    @app.get("/__tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        """Return a single task by id. 404 if not found."""
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    # Surface the task store on the app instance so tests can inject
    # pre-existing WORKING tasks (to exercise the cancel path).
    app.state.tasks = tasks  # type: ignore[attr-defined]
    return app


__all__ = ["build_remote_a2a_agent_app"]
