"""A2A HTTP routes — exposes A2AServer via A2A 1.0 JSON-RPC over HTTP.

Two routers are provided:

  * ``build_public_a2a_router(ctx)`` — mounts ``POST /a2a/v1/`` as the
    JSON-RPC endpoint (methods: ``message/send``, ``tasks/get``,
    ``tasks/cancel``).
  * ``build_well_known_router(ctx)`` — mounts
    ``GET /.well-known/agent-card.json`` for A2A 1.0 discovery.

Both routers are LAZY with respect to FastAPI: importing this module
does NOT require fastapi. The factory functions import fastapi on
first call.

CORE_ONLY safety: when ``ctx.a2a_server`` is ``None`` the JSON-RPC
router is not mounted at all (see ``create_app``); the well-known
router is always mounted but returns HTTP 404 if no server is
configured.

Note: ``from __future__ import annotations`` is intentionally NOT used
here so that FastAPI can resolve ``Request`` / ``Header`` types from
the enclosing closure at route-registration time.
"""

from typing import Any

from albra_seed.api.context import RequestContext

# JSON-RPC error codes (per spec).
_ERR_PARSE_ERROR = -32700
_ERR_INVALID_REQUEST = -32600
_ERR_METHOD_NOT_FOUND = -32601
_ERR_INVALID_PARAMS = -32602
_ERR_INTERNAL = -32603


def build_public_a2a_router(ctx: RequestContext) -> Any:
    """Build the JSON-RPC 2.0 router at ``/a2a/v1``.

    The router is only mounted by ``create_app`` when ``ctx.a2a_server``
    is not None. This function still builds a router regardless; the
    caller decides whether to include it.
    """
    from fastapi import APIRouter, Header, HTTPException, Request

    router = APIRouter(prefix="/a2a/v1", tags=["a2a"])
    a2a_server = ctx.a2a_server

    @router.post("/")
    async def a2a_jsonrpc(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """A2A 1.0 JSON-RPC endpoint.

        Dispatches by ``method``:
          * ``message/send`` → ``a2a_server.receive(task, token=...)``
          * ``tasks/get``    → ``a2a_server.get_status(id)``
          * ``tasks/cancel`` → ``a2a_server.cancel(id)``
        """
        from albra_seed.a2a.contract import A2ATask, TaskState
        from albra_seed.a2a.wire import (
            make_jsonrpc_response,
            task_status_to_wire,
            task_to_wire,
            wire_to_message,
        )

        if a2a_server is None:
            raise HTTPException(status_code=503, detail="A2A server not configured")

        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001
            return make_jsonrpc_response(
                error={"code": _ERR_PARSE_ERROR, "message": f"parse error: {exc}"},
                request_id=None,
            )

        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
            raise HTTPException(
                status_code=400,
                detail="Not a JSON-RPC 2.0 request (missing jsonrpc='2.0')",
            )

        method = body.get("method")
        params = body.get("params") or {}
        req_id = body.get("id")
        token = (
            authorization[len("Bearer "):].strip()
            if authorization and authorization.startswith("Bearer ")
            else (authorization.strip() if authorization else None)
        )

        if method == "message/send":
            wire_msg = params.get("message") or {}
            message = wire_to_message(wire_msg)
            metadata = dict(params.get("metadata") or {})
            session_id = params.get("sessionId")
            if session_id:
                metadata["session_id"] = session_id
            task = A2ATask(
                state=TaskState.SUBMITTED,
                message=message,
                metadata=metadata,
            )
            result = await a2a_server.receive(task, token=token)
            return make_jsonrpc_response(
                result=task_to_wire(result), request_id=req_id
            )

        if method == "tasks/get":
            task_id = params.get("id")
            if not task_id:
                return make_jsonrpc_response(
                    error={"code": _ERR_INVALID_PARAMS, "message": "missing 'id'"},
                    request_id=req_id,
                )
            status = await a2a_server.get_status(str(task_id))
            if status is None:
                return make_jsonrpc_response(
                    error={"code": _ERR_INVALID_PARAMS,
                           "message": f"task not found: {task_id}"},
                    request_id=req_id,
                )
            return make_jsonrpc_response(
                result=task_status_to_wire(status), request_id=req_id
            )

        if method == "tasks/cancel":
            task_id = params.get("id")
            if not task_id:
                return make_jsonrpc_response(
                    error={"code": _ERR_INVALID_PARAMS, "message": "missing 'id'"},
                    request_id=req_id,
                )
            cancelled = await a2a_server.cancel(str(task_id))
            if cancelled is None:
                return make_jsonrpc_response(
                    error={"code": _ERR_INVALID_PARAMS,
                           "message": f"task not found: {task_id}"},
                    request_id=req_id,
                )
            return make_jsonrpc_response(
                result=task_to_wire(cancelled), request_id=req_id
            )

        # Unknown method.
        return make_jsonrpc_response(
            error={"code": _ERR_METHOD_NOT_FOUND,
                   "message": f"method not found: {method}"},
            request_id=req_id,
        )

    return router


def build_well_known_router(ctx: RequestContext) -> Any:
    """Build the ``/.well-known/agent-card.json`` discovery router.

    Always mounted by ``create_app`` (even when ``ctx.a2a_server`` is
    None). Returns HTTP 404 when no A2A server is configured so
    discovery probes get a clean "not configured" signal.
    """
    from fastapi import APIRouter, HTTPException

    router = APIRouter(tags=["discovery"])

    @router.get("/.well-known/agent-card.json")
    async def get_agent_card() -> dict[str, Any]:
        """Return the AgentCard in A2A 1.0 wire format."""
        from albra_seed.a2a.wire import agent_card_to_wire

        if ctx.a2a_server is None:
            raise HTTPException(
                status_code=404, detail="A2A server not configured"
            )
        card = ctx.a2a_server.get_agent_card()
        return agent_card_to_wire(card)

    return router


__all__ = ["build_public_a2a_router", "build_well_known_router"]
