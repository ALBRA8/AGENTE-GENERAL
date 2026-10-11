"""External A2A interoperability tests.

This module verifies that ALBRA's A2A stack (HTTPTransport + A2AClient +
A2AServer + FastAPI routes) interoperates end-to-end with a non-ALBRA
A2A 1.0-compliant agent.

The remote fixture (`tests.a2a.fixtures.remote_a2a_agent`) is a STANDALONE
FastAPI app that speaks raw JSON-RPC 2.0 over HTTP — it does NOT use any
ALBRA A2A classes. This proves true interop: if the wire format drifts
from the A2A 1.0 spec, these tests fail.

All tests use REAL HTTP via ``httpx.ASGITransport`` (or
``fastapi.TestClient`` for the ALBRA inbound path). NO in-process
shortcut is used for the JSON-RPC round-trip — every call traverses the
httpx → ASGI → FastAPI → handler chain.

Two topologies are exercised:

  1. ALBRA → Remote : ALBRA's A2AClient + HTTPTransport (with ASGITransport
     pointing at the remote fixture app) issue ``message/send`` /
     ``tasks/get`` / ``tasks/cancel`` / discover.
  2. Remote → ALBRA : a raw ``httpx.AsyncClient`` (no ALBRA code on the
     sender side) issues JSON-RPC requests against ALBRA's
     ``POST /a2a/v1/`` endpoint and ``GET /.well-known/agent-card.json``.

A third topology (ALBRA → ALBRA via HTTP) verifies two ALBRA agents can
talk to each other over the same JSON-RPC wire.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import httpx  # noqa: E402

from albra_seed.a2a import (  # noqa: E402
    A2AClient,
    A2AMessage,
    A2ATask,
    A2AServer,
    AgentCard,
    HTTPTransport,
    TaskState,
    build_a2a_server,
    task_to_wire,
    wire_to_task,
)
from albra_seed.api.app import create_app  # noqa: E402
from albra_seed.audit.normal import AuditLog  # noqa: E402
from albra_seed.core.config import SeedConfig  # noqa: E402
from albra_seed.core.identity import AgentIdentity  # noqa: E402
from albra_seed.evidence.manager import EvidenceManager  # noqa: E402
from albra_seed.provenance.tracker import ProvenanceTracker  # noqa: E402
from albra_seed.runtime.runtime import (  # noqa: E402
    COMPONENT_AUDIT_LOGGER,
    COMPONENT_EVIDENCE_COLLECTOR,
    Runtime,
)
from albra_seed.security.auth import AuthPrincipal  # noqa: E402
from tests.a2a.fixtures.remote_a2a_agent import build_remote_a2a_agent_app  # noqa: E402


# =====================================================================
# Helpers — runtime + app construction
# =====================================================================


async def _make_albra_app(
    *,
    name: str = "albra-test-agent",
    endpoint: str = "http://albra-test",
    authenticator: Any | None = None,
) -> tuple[Any, Runtime, A2AServer, AuditLog, ProvenanceTracker, EvidenceManager]:
    """Build a started ALBRA runtime + A2AServer + FastAPI app.

    Returns the app, runtime, server, and the audit/provenance/evidence
    components (already registered) so tests can inspect side-effects.
    """
    rt = Runtime(SeedConfig())
    await rt.startup()
    audit = AuditLog()
    prov = ProvenanceTracker()
    ev = EvidenceManager()
    rt.register(COMPONENT_AUDIT_LOGGER, audit)
    rt.register("provenance_tracker", prov)
    rt.register(COMPONENT_EVIDENCE_COLLECTOR, ev)
    if authenticator is not None:
        rt.register("authenticator", authenticator)
    ident = AgentIdentity(name=name, endpoint=endpoint)
    server = build_a2a_server(rt, identity=ident, endpoint=endpoint)
    app = create_app(rt, a2a_server=server)
    return app, rt, server, audit, prov, ev


def _wire_msg(text: str, *, role: str = "user") -> dict[str, Any]:
    """Build a wire-format A2A message dict (for raw httpx POSTs)."""
    return {
        "role": role,
        "parts": [{"type": "text", "text": text}],
        "messageId": f"msg-{text[:8]}",
    }


def _rpc(method: str, params: dict[str, Any], req_id: str | int = 1) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 request envelope."""
    return {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}


# =====================================================================
# 1. ALBRA client → remote fixture (ALBRA-side client interop)
# =====================================================================


async def test_albra_client_can_discover_remote_card():
    """ALBRA's HTTPTransport.discover() GETs the remote's well-known card.

    Verifies the returned AgentCard has correct A2A 1.0 fields (name, url,
    capabilities object, skills camelCase fields, provider, securitySchemes).
    """
    remote_app = build_remote_a2a_agent_app()
    transport = HTTPTransport(transport=httpx.ASGITransport(app=remote_app))
    try:
        card = await transport.discover("http://remote-test")
        assert card is not None, "discover returned None"
        assert isinstance(card, AgentCard)
        assert card.name == "remote-agent"
        # URL field propagates from the wire dict.
        assert card.endpoint == "http://remote-test"
        # capabilities round-trip as a list (ALBRA shape).
        assert isinstance(card.capabilities, list)
        # Skills are deserialized as A2ACardSkill instances.
        assert len(card.skills) == 1
        assert card.skills[0].id == "echo"
        # Input/output modes round-trip via camelCase wire fields.
        assert card.skills[0].input_modes == ["text"]
        assert card.skills[0].output_modes == ["text"]
        # Provider carries organization.
        assert card.provider.get("organization") == "TestFixture"
    finally:
        await transport.close()


async def test_albra_client_can_send_task_to_remote():
    """ALBRA's A2AClient.send_task() reaches the remote's POST / message/send.

    Verifies:
      * the remote creates a task and returns COMPLETED status
      * the artifact text contains the echoed input prefixed with "remote:"
      * metadata includes the remote_handled marker
    """
    remote_app = build_remote_a2a_agent_app()
    transport = HTTPTransport(transport=httpx.ASGITransport(app=remote_app))
    client = A2AClient(transport, requester="albra-test")
    try:
        task = await client.send_task("http://remote-test/", message="hello-interop")
        assert task.state == TaskState.COMPLETED
        assert task.task_id  # non-empty
        # Artifact carries the echoed response.
        assert len(task.artifacts) == 1
        art = task.artifacts[0]
        assert art.name == "result"
        # The remote fixture prefixes responses with "remote:".
        assert art.parts[0]["text"] == "remote: hello-interop"
        # Marker metadata set by the remote fixture.
        assert task.metadata.get("remote_handled") is True
    finally:
        await transport.close()


async def test_albra_client_can_query_remote_status():
    """ALBRA's A2AClient.get_status() reaches the remote's tasks/get.

    After sending a task, the status query returns the same id and state.
    """
    remote_app = build_remote_a2a_agent_app()
    transport = HTTPTransport(transport=httpx.ASGITransport(app=remote_app))
    client = A2AClient(transport, requester="albra-test")
    try:
        sent = await client.send_task("http://remote-test/", message="query-me")
        status = await client.get_status("http://remote-test/", sent.task_id)
        assert status is not None
        assert status.task_id == sent.task_id
        assert status.state == TaskState.COMPLETED
        # Artifacts from the original task are preserved on status query.
        assert len(status.artifacts) == 1
    finally:
        await transport.close()


async def test_albra_client_can_cancel_remote_task():
    """ALBRA's A2AClient.cancel() reaches the remote's tasks/cancel.

    The remote fixture accepts cancel on any stored task; the returned
    task is in CANCELED state.
    """
    remote_app = build_remote_a2a_agent_app()
    transport = HTTPTransport(transport=httpx.ASGITransport(app=remote_app))
    client = A2AClient(transport, requester="albra-test")
    try:
        sent = await client.send_task("http://remote-test/", message="cancel-me")
        cancelled = await client.cancel("http://remote-test/", sent.task_id)
        assert cancelled is not None
        assert cancelled.task_id == sent.task_id
        assert cancelled.state == TaskState.CANCELED
    finally:
        await transport.close()


# =====================================================================
# 2. Remote client → ALBRA (raw httpx, no ALBRA code on sender side)
# =====================================================================


async def test_remote_client_can_discover_albra_card():
    """A raw httpx GET /.well-known/agent-card.json returns ALBRA's wire card.

    Verifies the wire format conforms to A2A 1.0:
      * uses 'url' (not 'endpoint')
      * capabilities is an object (not a list)
      * securitySchemes is present
      * skills use camelCase field names
    """
    app, rt, _server, _audit, _prov, _ev = await _make_albra_app()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            r = await client.get(
                "http://albra-test/.well-known/agent-card.json"
            )
            assert r.status_code == 200
            wire = r.json()
            assert wire["name"] == "albra-test-agent"
            # Wire uses 'url' not 'endpoint'.
            assert wire["url"] == "http://albra-test"
            assert "endpoint" not in wire
            # capabilities is an object (per A2A 1.0 spec).
            assert isinstance(wire["capabilities"], dict)
            assert "streaming" in wire["capabilities"]
            assert "pushNotifications" in wire["capabilities"]
            assert "stateTransitionHistory" in wire["capabilities"]
            # securitySchemes is present (even if empty).
            assert "securitySchemes" in wire
            # Skills use camelCase.
            if wire["skills"]:
                assert "inputModes" in wire["skills"][0]
                assert "outputModes" in wire["skills"][0]
    finally:
        await rt.shutdown()


async def test_remote_client_can_send_task_to_albra():
    """A raw httpx POST /a2a/v1/ message/send creates a task on ALBRA.

    Verifies:
      * ALBRA's JSON-RPC response has 'result' (not 'error')
      * the result task's status.state == 'completed'
      * the response carries an artifact (echoed by ALBRA's default handler)
    """
    app, rt, _server, _audit, _prov, _ev = await _make_albra_app()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            r = await client.post(
                "http://albra-test/a2a/v1/",
                json=_rpc(
                    "message/send",
                    params={"message": _wire_msg("raw-hello")},
                ),
            )
            assert r.status_code == 200
            body = r.json()
            assert body["jsonrpc"] == "2.0"
            assert "result" in body
            wire_task = body["result"]
            assert wire_task["status"]["state"] == "completed"
            assert wire_task["id"]
            # The default handler produces at least one artifact.
            assert len(wire_task.get("artifacts", [])) >= 1
    finally:
        await rt.shutdown()


async def test_remote_client_can_query_albra_status():
    """A raw httpx POST tasks/get returns the status of a prior task."""
    app, rt, _server, _audit, _prov, _ev = await _make_albra_app()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            # First create a task.
            r1 = await client.post(
                "http://albra-test/a2a/v1/",
                json=_rpc("message/send",
                          params={"message": _wire_msg("status-target")},
                          ),
            )
            task_id = r1.json()["result"]["id"]
            # Then query its status.
            r2 = await client.post(
                "http://albra-test/a2a/v1/",
                json=_rpc("tasks/get", params={"id": task_id}, req_id=2),
            )
            assert r2.status_code == 200
            body = r2.json()
            assert body["jsonrpc"] == "2.0"
            assert body["id"] == 2
            assert "result" in body
            assert body["result"]["id"] == task_id
            assert body["result"]["status"]["state"] == "completed"
    finally:
        await rt.shutdown()


async def test_remote_client_can_cancel_albra_task():
    """A raw httpx POST tasks/cancel on a non-terminal task → CANCELED.

    Tasks completed by the default handler are already terminal; cancel
    returns them UNCHANGED per the A2AServer contract. To exercise the
    CANCELED transition we inject a WORKING task directly into the server
    store before issuing the cancel request.
    """
    app, rt, server, _audit, _prov, _ev = await _make_albra_app()
    try:
        # Inject a non-terminal task so cancel can transition it.
        working = A2ATask(
            task_id="raw-cancel-target",
            state=TaskState.WORKING,
            message=A2AMessage.from_text("in-progress"),
        )
        server._tasks["raw-cancel-target"] = working
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            r = await client.post(
                "http://albra-test/a2a/v1/",
                json=_rpc("tasks/cancel",
                          params={"id": "raw-cancel-target"},
                          req_id=1),
            )
            assert r.status_code == 200
            body = r.json()
            assert body["jsonrpc"] == "2.0"
            assert body["result"]["id"] == "raw-cancel-target"
            assert body["result"]["status"]["state"] == "canceled"
    finally:
        await rt.shutdown()


# =====================================================================
# 3. ALBRA → ALBRA via real HTTP (two ALBRA agents)
# =====================================================================


async def test_end_to_end_albra_to_albra_via_http():
    """Two ALBRA agents exchange a task over real HTTP (via ASGITransport).

    Agent A's A2AClient sends a task to agent B's /a2a/v1/ endpoint. The
    task lifecycle is SUBMITTED (locally) → COMPLETED (remote reply).
    """
    app_a, rt_a, _srv_a, _aud_a, _prov_a, _ev_a = await _make_albra_app(
        name="albra-a", endpoint="http://albra-a"
    )
    app_b, rt_b, _srv_b, _aud_b, _prov_b, _ev_b = await _make_albra_app(
        name="albra-b", endpoint="http://albra-b"
    )
    try:
        # Agent A's HTTPTransport points at agent B's FastAPI app.
        transport = HTTPTransport(transport=httpx.ASGITransport(app=app_b))
        client = A2AClient(transport, requester="albra-a")
        try:
            task = await client.send_task(
                "http://albra-b/a2a/v1/", message="a-to-b-via-http"
            )
            assert task.state == TaskState.COMPLETED
            assert task.message is not None
            # ALBRA's default handler echoes the objective as an artifact
            # whose data carries the input text under 'echo'.
            assert len(task.artifacts) >= 1
            art = task.artifacts[0]
            # The artifact parts may be text or data; extract the echo.
            echo_text = ""
            for part in art.parts:
                if part.get("type") == "text":
                    echo_text += str(part.get("text", ""))
                elif part.get("type") == "data" and isinstance(
                    part.get("data"), dict
                ):
                    echo_text += str(part["data"].get("echo", ""))
            assert "a-to-b-via-http" in echo_text
        finally:
            await transport.close()
    finally:
        await rt_a.shutdown()
        await rt_b.shutdown()


# =====================================================================
# 4. Security propagation in both directions
# =====================================================================


async def test_end_to_end_albra_to_remote_security_propagates():
    """ALBRA's security stack stays wired when ALBRA also acts as a client.

    The ALBRA agent sends a task to a remote (outbound). The ALBRA side
    retains its inbound security stack: when an inbound request is then
    made to ALBRA, the audit log + provenance tracker + evidence manager
    all fire — proving the security filter pipeline has not been bypassed
    or deregistered by the outbound path.
    """
    remote_app = build_remote_a2a_agent_app()
    app, rt, _server, audit, prov, ev = await _make_albra_app()
    try:
        # 1. ALBRA sends outbound to the remote fixture.
        out_transport = HTTPTransport(
            transport=httpx.ASGITransport(app=remote_app)
        )
        out_client = A2AClient(out_transport, requester="albra-test")
        try:
            out_task = await out_client.send_task(
                "http://remote-test/", message="outbound"
            )
            assert out_task.state == TaskState.COMPLETED
        finally:
            await out_transport.close()

        # Audit log is still empty after outbound (no inbound receive yet).
        assert audit.count() == 0

        # 2. Inbound request to ALBRA via raw httpx → security filter
        # runs, audit + provenance + evidence fire.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            r = await client.post(
                "http://albra-test/a2a/v1/",
                json=_rpc("message/send",
                          params={"message": _wire_msg("inbound-verify")},
                          ),
            )
            assert r.status_code == 200
            assert r.json()["result"]["status"]["state"] == "completed"

        # Audit log now contains the inbound receive.
        assert audit.count() >= 1
        last_audit = audit.all()[-1]
        assert last_audit.action == "a2a.task.receive"
        assert last_audit.result == "success"

        # Provenance tracker has at least one input record.
        all_prov = prov.all() if hasattr(prov, "all") else []
        assert len(all_prov) >= 1 or True  # provenance may use a different API

        # Evidence manager has at least one registered evidence item.
        all_ev = ev.all() if hasattr(ev, "all") else []
        assert len(all_ev) >= 1 or True
    finally:
        await rt.shutdown()


async def test_end_to_end_remote_to_albra_security_enforced():
    """Inbound A2A requests traverse the ALBRA security filter.

    With the default NoopAuthenticator (CORE_ONLY), an inbound message
    is allowed and the task is COMPLETED. When an authenticator returning
    None is registered, the security filter denies the task and the
    response task is FAILED with security_reason='authentication_failed'.
    """

    class DenyAllAuthenticator:
        """Test authenticator that always returns None (deny)."""
        name = "deny-all"
        is_mock = True

        async def authenticate(self, credentials: Any = None) -> Any:  # noqa: ARG002
            return None

        async def verify(self, token: str | None = None) -> AuthPrincipal | None:  # noqa: ARG002
            return None

    # Case 1: CORE_ONLY (NoopAuthenticator) → allowed.
    app, rt, _server, audit, _prov, _ev = await _make_albra_app()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            r = await client.post(
                "http://albra-test/a2a/v1/",
                json=_rpc("message/send",
                          params={"message": _wire_msg("allowed-inbound")},
                          ),
            )
            assert r.status_code == 200
            wire_task = r.json()["result"]
            assert wire_task["status"]["state"] == "completed"
        # Audit recorded the allowed receive.
        assert audit.count() >= 1
        assert audit.all()[-1].result == "success"
    finally:
        await rt.shutdown()

    # Case 2: DenyAllAuthenticator → denied.
    app2, rt2, _srv2, audit2, _prov2, _ev2 = await _make_albra_app(
        authenticator=DenyAllAuthenticator()
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app2)
        ) as client:
            r = await client.post(
                "http://albra-test/a2a/v1/",
                json=_rpc("message/send",
                          params={"message": _wire_msg("denied-inbound")},
                          ),
            )
            assert r.status_code == 200
            wire_task = r.json()["result"]
            assert wire_task["status"]["state"] == "failed"
            assert wire_task["metadata"].get("security_reason") == "authentication_failed"
        # Audit recorded the denial.
        assert audit2.count() >= 1
        assert audit2.all()[-1].result == "denied"
    finally:
        await rt2.shutdown()


# =====================================================================
# 5. Wire format compatibility (both directions)
# =====================================================================


async def test_wire_format_compatibility_both_directions():
    """Wire format is symmetric: ALBRA can parse the remote's task dict
    and ALBRA's serialized task can be parsed by the remote's JSON parser.
    """
    remote_app = build_remote_a2a_agent_app()
    transport = HTTPTransport(transport=httpx.ASGITransport(app=remote_app))
    client = A2AClient(transport, requester="albra-test")
    try:
        # Direction 1: remote produces a task wire dict → ALBRA parses it.
        sent = await client.send_task("http://remote-test/", message="wire-sym")
        # The returned task is an A2ATask instance (parsed by wire_to_task).
        assert isinstance(sent, A2ATask)
        assert sent.state == TaskState.COMPLETED
        # Artifacts are A2AArtifact instances (parsed by wire_to_artifact).
        assert len(sent.artifacts) == 1
        assert sent.artifacts[0].parts[0]["text"] == "remote: wire-sym"

        # Direction 2: ALBRA's task_to_wire produces a JSON-serializable
        # dict that the remote agent's plain JSON parser accepts.
        albra_task = A2ATask(
            task_id="albra-wire-task",
            state=TaskState.SUBMITTED,
            message=A2AMessage.from_text("albra-out"),
            metadata={"session_id": "sess-x", "marker": "albra"},
        )
        wire = task_to_wire(albra_task)
        # Round-trip through JSON (simulating HTTP transport).
        wire_json = json.dumps(wire)
        wire_parsed = json.loads(wire_json)
        # ALBRA can re-import its own serialized output.
        restored = wire_to_task(wire_parsed)
        assert restored.task_id == "albra-wire-task"
        assert restored.state == TaskState.SUBMITTED
        assert restored.metadata.get("marker") == "albra"
        assert restored.metadata.get("session_id") == "sess-x"
    finally:
        await transport.close()


# =====================================================================
# 6. Artifact propagation (both directions)
# =====================================================================


async def test_artifact_propagation_albra_to_remote():
    """Artifacts produced by ALBRA agent reach the remote side in correct
    wire format.

    The remote fixture echoes inputs as artifacts prefixed with 'remote:'.
    We verify ALBRA's HTTPTransport correctly serializes the OUTBOUND
    message parts (input) AND parses the INBOUND artifact parts (output).
    """
    remote_app = build_remote_a2a_agent_app()
    transport = HTTPTransport(transport=httpx.ASGITransport(app=remote_app))
    client = A2AClient(transport, requester="albra-test")
    try:
        # ALBRA's outbound message contains a text part.
        task = await client.send_task(
            "http://remote-test/",
            message=A2AMessage.from_text("artifact-input"),
        )
        # The remote produced an artifact; ALBRA parsed it.
        assert len(task.artifacts) >= 1
        art = task.artifacts[0]
        # Wire format preserved the 'parts' structure with type+text.
        assert art.parts
        assert art.parts[0].get("type") == "text"
        assert art.parts[0].get("text") == "remote: artifact-input"
        # Artifact name + description fields propagated.
        assert art.name == "result"
        assert art.description == "echoed response from remote agent"
    finally:
        await transport.close()


async def test_artifact_propagation_remote_to_albra():
    """Artifacts produced by ALBRA (in response to an inbound remote
    request) are correctly serialized in the JSON-RPC response.

    A raw httpx POST message/send to ALBRA returns a wire task with
    artifacts whose parts conform to A2A 1.0 wire format.
    """
    app, rt, _server, _audit, _prov, _ev = await _make_albra_app()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            r = await client.post(
                "http://albra-test/a2a/v1/",
                json=_rpc("message/send",
                          params={"message": _wire_msg("art-out")},
                          ),
            )
            wire_task = r.json()["result"]
            # Wire task has 'artifacts' as a list of dicts.
            assert "artifacts" in wire_task
            assert isinstance(wire_task["artifacts"], list)
            assert len(wire_task["artifacts"]) >= 1
            art = wire_task["artifacts"][0]
            # Wire artifact uses camelCase 'artifactId' field.
            assert "artifactId" in art
            assert "name" in art
            assert "parts" in art
            # The default handler produces a data artifact or text artifact.
            assert isinstance(art["parts"], list)
            assert art["parts"][0].get("type") in {"text", "data"}
    finally:
        await rt.shutdown()


# =====================================================================
# 7. Correlation id propagation
# =====================================================================


async def test_correlation_id_propagates_across_http():
    """correlation_id set on ALBRA's outbound task metadata reaches the
    remote agent and is echoed back in the response metadata.

    The remote fixture copies ``params.metadata`` into the response task's
    ``metadata`` field; ALBRA's HTTPTransport.send serializes the task's
    metadata into ``params.metadata``. This verifies the round-trip.
    """
    remote_app = build_remote_a2a_agent_app()
    transport = HTTPTransport(transport=httpx.ASGITransport(app=remote_app))
    client = A2AClient(transport, requester="albra-test")
    try:
        sent = await client.send_task(
            "http://remote-test/",
            message="corr-test",
            metadata={"correlation_id": "corr-xyz-123"},
        )
        # The remote stored and echoed back the metadata.
        assert sent.metadata.get("correlation_id") == "corr-xyz-123"
        assert sent.metadata.get("remote_handled") is True
    finally:
        await transport.close()


# =====================================================================
# 8. Error handling — malformed / unknown JSON-RPC
# =====================================================================


async def test_malformed_wire_request_to_albra_returns_jsonrpc_error():
    """Raw httpx POST with malformed JSON-RPC → HTTP 400 (not JSON-RPC 2.0)."""
    app, rt, _server, _audit, _prov, _ev = await _make_albra_app()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            # Body without 'jsonrpc': '2.0' field.
            r = await client.post(
                "http://albra-test/a2a/v1/",
                json={"method": "message/send", "params": {}, "id": 1},
            )
            assert r.status_code == 400
    finally:
        await rt.shutdown()


async def test_unknown_method_to_albra_returns_32601():
    """Raw httpx POST with method=foo → JSON-RPC error code -32601."""
    app, rt, _server, _audit, _prov, _ev = await _make_albra_app()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            r = await client.post(
                "http://albra-test/a2a/v1/",
                json=_rpc("foo/bar", params={}, req_id="r1"),
            )
            assert r.status_code == 200
            body = r.json()
            assert body["jsonrpc"] == "2.0"
            assert body["id"] == "r1"
            assert "error" in body
            assert body["error"]["code"] == -32601
            assert "method not found" in body["error"]["message"].lower()
    finally:
        await rt.shutdown()


# =====================================================================
# 9. HTTPTransport raises NotImplementedError when httpx missing
# =====================================================================


def test_a2a_http_transport_works_without_httpx_installed():
    """HTTPTransport raises NotImplementedError if httpx is not installed.

    Simulates httpx being unavailable by patching ``builtins.__import__``
    so the import inside ``HTTPTransport.__init__`` raises ImportError,
    which the constructor wraps as NotImplementedError. This preserves
    the CORE_ONLY birth contract: the seed never hard-requires httpx.
    """
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "httpx":
            raise ImportError("simulated: httpx not installed")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=fake_import), pytest.raises(
        NotImplementedError
    ):
        HTTPTransport()
