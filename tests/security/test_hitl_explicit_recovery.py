"""Explicit post-restart recovery of exact approved calls, with API authorization."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from albra_seed.api.app import create_app
from albra_seed.approvals.engine import ApprovalEngine
from albra_seed.approvals.sqlite_store import SQLiteApprovalStore
from albra_seed.core.config import SeedConfig
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import RunContext, ToolCall, ToolResult
from albra_seed.runtime.agent import Agent
from albra_seed.runtime.runtime import Runtime


class _Tool:
    def __init__(self):
        self.contract = SimpleNamespace(name="external.send")
        self.calls = 0
        self.last_arguments = None

    async def execute(self, arguments, *, context):
        self.calls += 1
        self.last_arguments = dict(arguments)
        return ToolResult(
            call_id="recover-call-1", success=True, output={"sent": True}
        )


class _Authenticator:
    async def verify(self, token):
        if token != "valid":
            return None
        return SimpleNamespace(
            identity="reviewer-1", roles=["approver"], is_anonymous=False
        )


class _RuleSet:
    def __init__(self, allowed_permissions):
        self.allowed_permissions = set(allowed_permissions)

    def first_match(self, *, action_name, actor_id, risk):
        if action_name in self.allowed_permissions:
            return SimpleNamespace(required_permission=action_name)
        return None


class _Policy:
    def __init__(self, allowed_permissions):
        self.rule_set = _RuleSet(allowed_permissions)

    async def evaluate(self, action, **kwargs):
        return SimpleNamespace(allowed=True, decision="allow")


class _Firewall:
    async def check(self, call, *, context):
        return SimpleNamespace(allowed=True)


async def _build_recovery_setup(tmp_path, *, allowed_permissions=("approval:resume",)):
    runtime = Runtime(config=SeedConfig())
    store = SQLiteApprovalStore(tmp_path / "approvals.sqlite3")
    engine = ApprovalEngine(store=store)
    runtime.register("approval_engine", engine)
    runtime.register("authenticator", _Authenticator())
    runtime.register("policy_engine", _Policy(allowed_permissions))
    runtime.register("firewall", _Firewall())

    tool = _Tool()
    runtime.register_tool(tool)
    identity = AgentIdentity(name="recovery-test-agent")
    agent = Agent(identity=identity, runtime=runtime)
    call = ToolCall(
        id="recover-call-1",
        tool_name="external.send",
        arguments={"to": "recipient@example.test", "body": "original approved message"},
    )
    context = RunContext(agent_id=identity.agent_id, task="send a message")
    request = await engine.request(call, context=context)
    await engine.approve(request.approval_id, approver="reviewer-1")
    return runtime, store, engine, agent, tool, identity, request


@pytest.mark.asyncio
async def test_restart_recovery_dispatches_only_the_exact_saved_call(tmp_path):
    runtime, store, old_engine, old_agent, tool, identity, request = (
        await _build_recovery_setup(tmp_path)
    )

    # Simulate process restart: rebuild the engine from the same durable file
    # and bind a fresh Agent instance to it. No provider/model is registered.
    recovered_engine = ApprovalEngine(store=SQLiteApprovalStore(store.db_path))
    runtime.register("approval_engine", recovered_engine)
    recovered_agent = Agent(identity=identity, runtime=runtime)

    result = await recovered_agent.resume_approved_approval(
        request.approval_id, resumed_by="reviewer-1"
    )
    second = await recovered_agent.resume_approved_approval(
        request.approval_id, resumed_by="reviewer-1"
    )

    assert result.success is True
    assert second.success is False
    assert tool.calls == 1
    assert tool.last_arguments == {
        "to": "recipient@example.test", "body": "original approved message"
    }


@pytest.mark.asyncio
async def test_recovery_rejects_tampered_arguments_digest(tmp_path):
    runtime, store, engine, agent, tool, identity, request = (
        await _build_recovery_setup(tmp_path)
    )
    saved = engine.get(request.approval_id)
    payload = saved.model_dump(mode="json")
    payload["context_ref"]["arguments"]["to"] = "attacker@example.test"
    with store._connect() as connection:
        connection.execute(
            "UPDATE approvals SET request_json = ? WHERE approval_id = ?",
            (json.dumps(payload), request.approval_id),
        )

    with pytest.raises(ValueError, match="argument"):
        await agent.resume_approved_approval(
            request.approval_id, resumed_by="reviewer-1"
        )
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_recovery_rejects_tampered_arguments_and_recomputed_digest(tmp_path):
    from albra_seed.approvals.suspension import canonical_arguments_sha256

    runtime, store, engine, agent, tool, identity, request = (
        await _build_recovery_setup(tmp_path)
    )
    saved = engine.get(request.approval_id)
    payload = saved.model_dump(mode="json")
    payload["context_ref"]["arguments"]["to"] = "attacker@example.test"
    payload["context_ref"]["arguments_sha256"] = canonical_arguments_sha256(
        payload["context_ref"]["arguments"]
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE approvals SET request_json = ? WHERE approval_id = ?",
            (json.dumps(payload), request.approval_id),
        )

    with pytest.raises(ValueError, match="evidence"):
        await agent.resume_approved_approval(
            request.approval_id, resumed_by="reviewer-1"
        )
    assert tool.calls == 0


@pytest.mark.asyncio
async def test_recovery_api_requires_authentication_and_explicit_permission(tmp_path):
    runtime, store, engine, agent, tool, identity, request = (
        await _build_recovery_setup(tmp_path, allowed_permissions=())
    )
    app = create_app(runtime, agents={identity.agent_id: agent})
    with TestClient(app) as client:
        unauthenticated = client.post(
            f"/api/v1/approvals/{request.approval_id}/resume"
        )
        forbidden = client.post(
            f"/api/v1/approvals/{request.approval_id}/resume",
            headers={"Authorization": "Bearer valid"},
        )
        assert unauthenticated.status_code == 401
        assert forbidden.status_code == 403
        assert tool.calls == 0

        # Install an explicit, action-specific grant before trying recovery.
        runtime.register("policy_engine", _Policy(("approval:resume",)))
        resumed = client.post(
            f"/api/v1/approvals/{request.approval_id}/resume",
            headers={"Authorization": "Bearer valid"},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "completed"
        assert tool.calls == 1
