"""A2A FastAPI route tests.

Verifies the HTTP routes that expose A2AServer via A2A 1.0 JSON-RPC:
  * GET /.well-known/agent-card.json — discovery
  * POST /a2a/v1/ — JSON-RPC (message/send, tasks/get, tasks/cancel)

Uses ``fastapi.TestClient`` so no live server is required. The A2AServer
is wired to a real (CORE_ONLY) runtime so the full pipeline runs.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # type: ignore[import-untyped]

from albra_seed.a2a import A2AMessage, A2AServer, A2ATask, TaskState, build_a2a_server
from albra_seed.api.app import create_app
from albra_seed.audit.normal import AuditLog
from albra_seed.core.config import SeedConfig
from albra_seed.core.identity import AgentIdentity
from albra_seed.evidence.manager import EvidenceManager
from albra_seed.handoff.manager import HandoffManager
from albra_seed.provenance.tracker import ProvenanceTracker
from albra_seed.runtime.runtime import (
    COMPONENT_AUDIT_LOGGER,
    COMPONENT_EVIDENCE_COLLECTOR,
    Runtime,
)
from albra_seed.security.auth import AuthPrincipal


# =====================================================================
# Helpers
# =====================================================================


def _identity() -> AgentIdentity:
    return AgentIdentity(
        name="routes-agent",
        version="3.1.0",
        capabilities=["a2a.task.receive"],
        skills=["echo"],
        interfaces=["text"],
        endpoint="https://routes.example/a2a",
    )


def _make_runtime() -> Runtime:
    """Build a CORE_ONLY runtime with audit/provenance/evidence registered."""
    rt = Runtime(SeedConfig())
    # Manually register the components the A2AServer needs (avoid
    # awaiting startup() so the fixture is sync-friendly).
    return rt


async def _make_started_runtime() -> Runtime:
    """Build a started runtime with audit/provenance/evidence registered."""
    rt = Runtime(SeedConfig())
    await rt.startup()
    rt.register(COMPONENT_AUDIT_LOGGER, AuditLog())
    rt.register("provenance_tracker", ProvenanceTracker())
    rt.register(COMPONENT_EVIDENCE_COLLECTOR, EvidenceManager())
    return rt


async def _make_app(*, a2a_server: Any = None) -> Any:
    """Build a FastAPI app, optionally with an A2AServer."""
    rt = await _make_started_runtime()
    try:
        return create_app(rt, a2a_server=a2a_server), rt
    except Exception:
        await rt.shutdown()
        raise


def _make_server_sync(runtime: Runtime) -> A2AServer:
    """Build an A2AServer bound to the given runtime (sync helper)."""
    return A2AServer(
        runtime,
        identity=_identity(),
        handoff_manager=HandoffManager(),
    )


def _send_rpc(client: TestClient, method: str, params: dict[str, Any],
              req_id: str | int = 1, token: str | None = None) -> dict[str, Any]:
    """POST a JSON-RPC envelope to /a2a/v1/ and return the parsed body."""
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}
    r = client.post("/a2a/v1/", json=body, headers=headers)
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
    return r.json()


# =====================================================================
# 1. Well-known discovery route
# =====================================================================


def test_well_known_agent_card_returns_wire_format():
    """GET /.well-known/agent-card.json returns wire dict with url, capabilities."""
    import asyncio

    async def run():
        rt = await _make_started_runtime()
        try:
            server = _make_server_sync(rt)
            app = create_app(rt, a2a_server=server)
            with TestClient(app) as client:
                r = client.get("/.well-known/agent-card.json")
                assert r.status_code == 200
                body = r.json()
                # Wire field names
                assert body["name"] == "routes-agent"
                assert body["url"] == "https://routes.example/a2a"
                assert "endpoint" not in body
                assert isinstance(body["capabilities"], dict)
                assert "streaming" in body["capabilities"]
                assert "securitySchemes" in body
        finally:
            await rt.shutdown()

    asyncio.run(run())


def test_well_known_agent_card_404_if_no_server():
    """When a2a_server=None, /.well-known/agent-card.json returns 404."""
    import asyncio

    async def run():
        rt = await _make_started_runtime()
        try:
            app = create_app(rt, a2a_server=None)
            with TestClient(app) as client:
                r = client.get("/.well-known/agent-card.json")
                assert r.status_code == 404
        finally:
            await rt.shutdown()

    asyncio.run(run())


# =====================================================================
# 2. JSON-RPC message/send
# =====================================================================


def test_a2a_jsonrpc_message_send():
    """POST /a2a/v1/ with message/send → task returned."""
    import asyncio

    async def run():
        rt = await _make_started_runtime()
        try:
            server = _make_server_sync(rt)
            app = create_app(rt, a2a_server=server)
            with TestClient(app) as client:
                msg = {"role": "user", "parts": [{"type": "text", "text": "hello"}],
                       "messageId": "m-1"}
                body = _send_rpc(client, "message/send",
                                 params={"message": msg}, req_id="r1")
                assert body["jsonrpc"] == "2.0"
                assert body["id"] == "r1"
                assert "result" in body
                wire_task = body["result"]
                assert "id" in wire_task
                assert wire_task["status"]["state"] == "completed"
        finally:
            await rt.shutdown()

    asyncio.run(run())


# =====================================================================
# 3. JSON-RPC tasks/get
# =====================================================================


def test_a2a_jsonrpc_tasks_get():
    """POST /a2a/v1/ with tasks/get → status returned."""
    import asyncio

    async def run():
        rt = await _make_started_runtime()
        try:
            server = _make_server_sync(rt)
            app = create_app(rt, a2a_server=server)
            with TestClient(app) as client:
                # First send a task so it exists.
                msg = {"role": "user", "parts": [{"type": "text", "text": "hi"}],
                       "messageId": "m-get"}
                send_body = _send_rpc(client, "message/send",
                                      params={"message": msg}, req_id="r1")
                task_id = send_body["result"]["id"]
                # Now query its status.
                body = _send_rpc(client, "tasks/get",
                                 params={"id": task_id}, req_id="r2")
                assert body["jsonrpc"] == "2.0"
                assert body["id"] == "r2"
                assert "result" in body
                assert body["result"]["id"] == task_id
                assert body["result"]["status"]["state"] == "completed"
        finally:
            await rt.shutdown()

    asyncio.run(run())


# =====================================================================
# 4. JSON-RPC tasks/cancel
# =====================================================================


def test_a2a_jsonrpc_tasks_cancel():
    """POST /a2a/v1/ with tasks/cancel → cancelled task.

    Injects a non-terminal (WORKING) task into the server's store so
    cancel has something to transition to CANCELED. (Tasks that are
    already terminal — e.g. COMPLETED from message/send — are returned
    unchanged by cancel, per the A2AServer contract.)
    """
    import asyncio

    async def run():
        rt = await _make_started_runtime()
        try:
            server = _make_server_sync(rt)
            # Inject a WORKING task directly so cancel can transition it.
            working = A2ATask(
                task_id="cancel-me",
                state=TaskState.WORKING,
                message=A2AMessage.from_text("in progress"),
            )
            server._tasks["cancel-me"] = working
            app = create_app(rt, a2a_server=server)
            with TestClient(app) as client:
                body = _send_rpc(client, "tasks/cancel",
                                 params={"id": "cancel-me"}, req_id="r2")
                assert body["jsonrpc"] == "2.0"
                assert body["id"] == "r2"
                assert "result" in body
                assert body["result"]["id"] == "cancel-me"
                assert body["result"]["status"]["state"] == "canceled"
        finally:
            await rt.shutdown()

    asyncio.run(run())


# =====================================================================
# 5. JSON-RPC error handling
# =====================================================================


def test_a2a_jsonrpc_unknown_method_returns_error():
    """Unknown method → JSON-RPC error -32601."""
    import asyncio

    async def run():
        rt = await _make_started_runtime()
        try:
            server = _make_server_sync(rt)
            app = create_app(rt, a2a_server=server)
            with TestClient(app) as client:
                body = _send_rpc(client, "foo/bar", params={}, req_id="r1")
                assert body["jsonrpc"] == "2.0"
                assert body["id"] == "r1"
                assert "error" in body
                assert body["error"]["code"] == -32601
        finally:
            await rt.shutdown()

    asyncio.run(run())


def test_a2a_jsonrpc_missing_jsonrpc_field():
    """Body without jsonrpc=2.0 → HTTP 400."""
    import asyncio

    async def run():
        rt = await _make_started_runtime()
        try:
            server = _make_server_sync(rt)
            app = create_app(rt, a2a_server=server)
            with TestClient(app) as client:
                r = client.post("/a2a/v1/", json={"method": "message/send",
                                                  "params": {}, "id": 1})
                assert r.status_code == 400
        finally:
            await rt.shutdown()

    asyncio.run(run())


# =====================================================================
# 6. Authorization header propagation
# =====================================================================


def test_a2a_jsonrpc_authorization_header_propagates_to_security_filter():
    """Authorization: Bearer <token> reaches the A2ASecurityFilter.

    Registers a recording authenticator on the runtime; verifies the
    token received by the authenticator matches the one sent.
    """
    import asyncio

    class RecordingAuthenticator:
        def __init__(self) -> None:
            self.last_token: str | None = None

        async def verify(self, token: str | None = None) -> AuthPrincipal | None:
            self.last_token = token
            return AuthPrincipal(identity="anon", roles=[])

    async def run():
        rt = await _make_started_runtime()
        try:
            auth = RecordingAuthenticator()
            rt.register("authenticator", auth)
            server = _make_server_sync(rt)
            app = create_app(rt, a2a_server=server)
            with TestClient(app) as client:
                msg = {"role": "user", "parts": [{"type": "text", "text": "hi"}],
                       "messageId": "m-auth"}
                _send_rpc(client, "message/send", params={"message": msg},
                          req_id="r1", token="my-secret-token")
                # The authenticator received the token from the Authorization header.
                assert auth.last_token == "my-secret-token"
        finally:
            await rt.shutdown()

    asyncio.run(run())


# =====================================================================
# 7. Routes not mounted when a2a_server=None
# =====================================================================


def test_a2a_routes_not_mounted_if_no_server():
    """create_app with a2a_server=None → /a2a/v1/ returns 404."""
    import asyncio

    async def run():
        rt = await _make_started_runtime()
        try:
            app = create_app(rt, a2a_server=None)
            with TestClient(app) as client:
                r = client.post("/a2a/v1/", json={
                    "jsonrpc": "2.0", "method": "message/send",
                    "params": {}, "id": 1,
                })
                assert r.status_code == 404
        finally:
            await rt.shutdown()

    asyncio.run(run())


# =====================================================================
# 8. build_a2a_server exposition helper
# =====================================================================


def test_build_a2a_server_creates_wired_server():
    """build_a2a_server returns a fully-wired A2AServer with endpoint attribute."""
    import asyncio

    async def run():
        rt = await _make_started_runtime()
        try:
            ident = _identity()
            server = build_a2a_server(rt, identity=ident, endpoint="https://custom/a2a")
            assert isinstance(server, A2AServer)
            assert server.endpoint == "https://custom/a2a"
            # The server can produce an agent card.
            card = server.get_agent_card()
            assert card.name == "routes-agent"
        finally:
            await rt.shutdown()

    asyncio.run(run())
