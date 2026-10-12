"""A2A 1.0 wire-format + HTTP transport tests.

Verifies:
  * Wire serialization (agent_card_to_wire, task_to_wire, etc.)
  * JSON-RPC envelope helpers
  * HTTPTransport real httpx calls (via MockTransport — no network)
  * Error propagation from JSON-RPC error responses

These tests are PURE — no live HTTP, no live A2AServer. They exercise
the wire-format translation and the transport layer in isolation.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from albra_seed.a2a import (
    A2AArtifact,
    A2ACardSkill,
    A2AMessage,
    A2ATask,
    AgentCard,
    HTTPTransport,
    TaskState,
    agent_card_to_wire,
    artifact_to_wire,
    make_jsonrpc_request,
    make_jsonrpc_response,
    message_to_wire,
    task_to_wire,
    wire_to_agent_card,
    wire_to_task,
)
from albra_seed.a2a.wire import WIRE_STATE_MAP


# =====================================================================
# Helpers
# =====================================================================


def _card() -> AgentCard:
    return AgentCard(
        name="wire-agent",
        description="test card",
        version="2.0.0",
        endpoint="https://example.com/a2a",
        capabilities=["a2a.task.receive"],
        skills=[
            A2ACardSkill(
                id="echo",
                name="Echo",
                description="echo skill",
                tags=["test"],
                input_modes=["text"],
                output_modes=["text"],
            )
        ],
        default_input_modes=["text"],
        default_output_modes=["text"],
        authentication={"schemes": {"bearer": {"type": "http"}}},
        provider={"organization": "ALBRA"},
        metadata={"agent_id": "agent-1"},
    )


def _task() -> A2ATask:
    return A2ATask(
        task_id="task-wire-1",
        state=TaskState.WORKING,
        message=A2AMessage.from_text("hello", role="user"),
        artifacts=[A2AArtifact.from_text("result text", name="out")],
        metadata={"session_id": "sess-1", "custom": "value"},
    )


# =====================================================================
# 1. AgentCard wire serialization
# =====================================================================


def test_agent_card_to_wire_basic():
    """Wire dict uses spec field names: url, capabilities (object), securitySchemes."""
    wire = agent_card_to_wire(_card())
    assert wire["name"] == "wire-agent"
    assert wire["version"] == "2.0.0"
    # spec uses 'url' not 'endpoint'
    assert wire["url"] == "https://example.com/a2a"
    assert "endpoint" not in wire
    # capabilities is an object with spec keys
    assert isinstance(wire["capabilities"], dict)
    assert "streaming" in wire["capabilities"]
    assert "pushNotifications" in wire["capabilities"]
    assert "stateTransitionHistory" in wire["capabilities"]
    # securitySchemes (not authentication)
    assert "securitySchemes" in wire
    assert "authentication" not in wire
    assert wire["securitySchemes"] == {"bearer": {"type": "http"}}
    assert wire["provider"] == {"organization": "ALBRA"}


def test_agent_card_to_wire_skills():
    """Skills use spec camelCase field names: inputModes, outputModes."""
    wire = agent_card_to_wire(_card())
    skills = wire["skills"]
    assert len(skills) == 1
    s = skills[0]
    assert s["id"] == "echo"
    assert s["name"] == "Echo"
    assert s["description"] == "echo skill"
    assert s["tags"] == ["test"]
    assert s["inputModes"] == ["text"]
    assert s["outputModes"] == ["text"]


def test_wire_to_agent_card_roundtrip():
    """serialize → deserialize preserves key fields."""
    original = _card()
    wire = agent_card_to_wire(original)
    restored = wire_to_agent_card(wire)
    assert restored.name == original.name
    assert restored.version == original.version
    assert restored.endpoint == original.endpoint
    assert restored.description == original.description
    # capabilities round-trip via x-albra-metadata
    assert restored.capabilities == original.capabilities
    # skills round-trip
    assert len(restored.skills) == len(original.skills)
    assert restored.skills[0].id == original.skills[0].id
    assert restored.skills[0].name == original.skills[0].name
    assert restored.skills[0].input_modes == original.skills[0].input_modes
    assert restored.skills[0].output_modes == original.skills[0].output_modes
    # default modes round-trip
    assert restored.default_input_modes == original.default_input_modes
    assert restored.default_output_modes == original.default_output_modes


# =====================================================================
# 2. Task wire serialization
# =====================================================================


def test_task_to_wire_uses_spec_field_names():
    """Wire task uses 'id' (not task_id), 'status' (not state), 'sessionId'."""
    wire = task_to_wire(_task())
    assert wire["id"] == "task-wire-1"
    assert "task_id" not in wire
    assert "state" not in wire
    assert isinstance(wire["status"], dict)
    assert wire["status"]["state"] == "working"
    # sessionId extracted from metadata
    assert wire["sessionId"] == "sess-1"
    # session_id removed from metadata
    assert "session_id" not in wire["metadata"]
    assert wire["metadata"]["custom"] == "value"


def test_wire_to_task_roundtrip():
    """serialize → deserialize preserves task_id, state, message, artifacts."""
    original = _task()
    wire = task_to_wire(original)
    restored = wire_to_task(wire)
    assert restored.task_id == original.task_id
    assert restored.state == original.state
    assert restored.message is not None
    assert restored.message.text == original.message.text
    assert restored.message.role == original.message.role
    assert len(restored.artifacts) == len(original.artifacts)
    assert restored.artifacts[0].name == original.artifacts[0].name
    # session_id restored to metadata
    assert restored.metadata.get("session_id") == "sess-1"


def test_wire_state_mapping_all_states():
    """All 6 TaskState values map to correct kebab-case wire strings."""
    expected = {
        TaskState.SUBMITTED: "submitted",
        TaskState.WORKING: "working",
        TaskState.INPUT_REQUIRED: "input-required",
        TaskState.COMPLETED: "completed",
        TaskState.CANCELED: "canceled",
        TaskState.FAILED: "failed",
    }
    assert WIRE_STATE_MAP == expected
    # round-trip each state
    for state in TaskState:
        task = A2ATask(task_id="t", state=state, message=A2AMessage.from_text("x"))
        wire = task_to_wire(task)
        restored = wire_to_task(wire)
        assert restored.state == state, f"state {state} did not round-trip"


# =====================================================================
# 3. JSON-RPC 2.0 envelope helpers
# =====================================================================


def test_make_jsonrpc_request_format():
    """Request envelope has jsonrpc=2.0, method, params, id."""
    rpc = make_jsonrpc_request("message/send", params={"message": {}}, request_id="r1")
    assert rpc["jsonrpc"] == "2.0"
    assert rpc["method"] == "message/send"
    assert rpc["params"] == {"message": {}}
    assert rpc["id"] == "r1"


def test_make_jsonrpc_response_success():
    """Success response has 'result' and no 'error'."""
    resp = make_jsonrpc_response(result={"id": "t1"}, request_id="r1")
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == "r1"
    assert resp["result"] == {"id": "t1"}
    assert "error" not in resp


def test_make_jsonrpc_response_error():
    """Error response has 'error' with code + message and no 'result'."""
    resp = make_jsonrpc_response(
        error={"code": -32601, "message": "method not found"},
        request_id="r1",
    )
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == "r1"
    assert resp["error"]["code"] == -32601
    assert resp["error"]["message"] == "method not found"
    assert "result" not in resp


# =====================================================================
# 4. HTTPTransport — real httpx via MockTransport
# =====================================================================


def test_http_transport_init_with_httpx():
    """When httpx IS installed, HTTPTransport instantiates without error."""
    pytest.importorskip("httpx")
    transport = HTTPTransport()
    assert transport is not None
    # health should report alive (client not closed)
    import asyncio
    alive = asyncio.run(transport.health("https://example.com"))
    assert alive is True
    asyncio.run(transport.close())


def _mock_handler(canned_result: dict[str, Any] | None = None,
                  error: dict[str, Any] | None = None):
    """Build an httpx.MockTransport handler that captures the RPC envelope."""
    import httpx

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        rpc = json.loads(body) if body else {}
        captured["rpc"] = rpc
        captured["headers"] = dict(request.headers)
        resp_body: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc.get("id")}
        if error is not None:
            resp_body["error"] = error
        else:
            resp_body["result"] = canned_result or {}
        return httpx.Response(200, json=resp_body)

    return handler, captured


async def test_http_transport_send_real_http():
    """send() posts JSON-RPC message/send and returns the wire task."""
    pytest.importorskip("httpx")
    import httpx

    canned = task_to_wire(A2ATask(
        task_id="rt-1", state=TaskState.COMPLETED,
        message=A2AMessage.from_text("done"),
    ))
    handler, captured = _mock_handler(canned_result=canned)
    transport = HTTPTransport(transport=httpx.MockTransport(handler))
    try:
        task = A2ATask(
            task_id="rt-1", message=A2AMessage.from_text("hi"),
            requester="x", target="y",
        )
        result = await transport.send("https://target.example/a2a", task)
        # The RPC envelope was message/send
        assert captured["rpc"]["method"] == "message/send"
        assert captured["rpc"]["jsonrpc"] == "2.0"
        assert captured["rpc"]["id"] == "rt-1"
        # params contain the wire message
        assert "message" in captured["rpc"]["params"]
        # Result is the wire task deserialized
        assert result.task_id == "rt-1"
        assert result.state == TaskState.COMPLETED
    finally:
        await transport.close()


async def test_http_transport_get_status_real_http():
    """get_status() posts JSON-RPC tasks/get and returns A2ATaskStatus."""
    pytest.importorskip("httpx")
    import httpx

    canned = {
        "id": "gs-1",
        "status": {"state": "working", "message": None},
        "artifacts": [],
    }
    handler, captured = _mock_handler(canned_result=canned)
    transport = HTTPTransport(transport=httpx.MockTransport(handler))
    try:
        status = await transport.get_status("https://target.example/a2a", "gs-1")
        assert captured["rpc"]["method"] == "tasks/get"
        assert captured["rpc"]["params"] == {"id": "gs-1"}
        assert status is not None
        assert status.task_id == "gs-1"
        assert status.state == TaskState.WORKING
    finally:
        await transport.close()


async def test_http_transport_cancel_real_http():
    """cancel() posts JSON-RPC tasks/cancel and returns the cancelled task."""
    pytest.importorskip("httpx")
    import httpx

    canned = task_to_wire(A2ATask(
        task_id="cn-1", state=TaskState.CANCELED,
        message=A2AMessage.from_text("bye"),
    ))
    handler, captured = _mock_handler(canned_result=canned)
    transport = HTTPTransport(transport=httpx.MockTransport(handler))
    try:
        result = await transport.cancel("https://target.example/a2a", "cn-1")
        assert captured["rpc"]["method"] == "tasks/cancel"
        assert captured["rpc"]["params"] == {"id": "cn-1"}
        assert result is not None
        assert result.task_id == "cn-1"
        assert result.state == TaskState.CANCELED
    finally:
        await transport.close()


async def test_http_transport_discover_agent_card():
    """discover() GETs /.well-known/agent-card.json and returns AgentCard."""
    pytest.importorskip("httpx")
    import httpx

    wire_card = agent_card_to_wire(_card())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/.well-known/agent-card.json"
        return httpx.Response(200, json=wire_card)

    transport = HTTPTransport(transport=httpx.MockTransport(handler))
    try:
        card = await transport.discover("https://target.example")
        assert card is not None
        assert card.name == "wire-agent"
        assert card.endpoint == "https://example.com/a2a"
    finally:
        await transport.close()


async def test_http_transport_propagates_error_response():
    """JSON-RPC error response → task FAILED with error in metadata."""
    pytest.importorskip("httpx")
    import httpx

    err = {"code": -32602, "message": "invalid params"}
    handler, _ = _mock_handler(error=err)
    transport = HTTPTransport(transport=httpx.MockTransport(handler))
    try:
        task = A2ATask(
            task_id="err-1", message=A2AMessage.from_text("hi"),
            requester="x", target="y",
        )
        result = await transport.send("https://target.example/a2a", task)
        assert result.state == TaskState.FAILED
        assert "error" in result.metadata
        assert result.metadata["error"]["code"] == -32602
    finally:
        await transport.close()


async def test_http_transport_send_includes_authorization_header():
    """When token is passed, Authorization: Bearer header is set."""
    pytest.importorskip("httpx")
    import httpx

    canned = task_to_wire(A2ATask(task_id="auth-1", state=TaskState.COMPLETED))
    handler, captured = _mock_handler(canned_result=canned)
    transport = HTTPTransport(transport=httpx.MockTransport(handler))
    try:
        task = A2ATask(
            task_id="auth-1", message=A2AMessage.from_text("hi"),
            requester="x", target="y",
        )
        await transport.send("https://target.example/a2a", task, token="secret-token")
        assert captured["headers"].get("authorization") == "Bearer secret-token"
    finally:
        await transport.close()
