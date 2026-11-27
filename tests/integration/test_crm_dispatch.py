"""R-AG-1 — POST /crm/dispatch integration tests (S4.4).

Real ASGI app (create_app + TestClient), no mocks of the bridge: every
scenario drives the real endpoint pipeline — config fail-closed, bearer,
HMAC (±300 s, timing-safe), contract validation, tenant identity
(X-Org-Id echo + D-014 agentId), per-org rate limit, RBAC, firewall,
idempotency, organization-scoped memory persistence (T9) and the typed
error envelope from the FROZEN CRM catalog (D-013).

These tests pair with the CRM-side suite
``services/agent-gateway/tests/cerebro-real.test.ts`` which drives the
SAME pipeline over a real uvicorn process (cross-language, cross-repo).
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import time
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # type: ignore[import-untyped]

from albra_seed.api.app import create_app
from albra_specialization.crm.bridge import install_crm_dispatch
from albra_seed.core.config import SeedConfig
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import MemoryType, PolicyDecision, RiskLevel
from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.memory.manager import MemoryManager
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule, PolicyRuleSet
from albra_seed.runtime.agent import Agent
from albra_seed.runtime.runtime import Runtime
from albra_seed.security.auth import AuthPrincipal
from albra_seed.security.permissions import PermissionRegistry
from albra_seed.security.roles import Role, RoleRegistry

SECRET = "secreto-s44-dispatch"
TOKEN = "valid-crm-bridge-token"
ORG_A = "org_aaaa1111"
ORG_B = "org_bbbb2222"
AGENT_A = f"org:{ORG_A}#ventas"
AGENT_B = f"org:{ORG_B}#ventas"


def _base_event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": "conversation.turn.received",
        "event_id": "evt_s44_0001",
        "conversation": {"id": "cv_s44_1", "channel": "whatsapp", "test": True},
        "contact": {
            "id": "ct_s44_1",
            "identity": "573009998877",
            "name": "Prueba S4.4",
        },
        "organization": {
            "id": ORG_A,
            "agentId": AGENT_A,
            "timezone": "America/Bogota",
            "locale": "es",
        },
        "messageIds": ["msg_s44_1"],
        "turn": {
            "text": "¿Cuánto cuesta el plan?",
            "media": [],
            "sinceLastReplyMs": 5000,
        },
        "capabilities": {
            "booking": False,
            "stageMoves": [],
            "windowOpenUntil": "2026-12-31T12:00:00Z",
        },
    }
    event.update(overrides)
    return event


class _BridgeAuthenticator:
    async def verify(self, token: str | None) -> AuthPrincipal | None:
        if token != TOKEN:
            return None
        return AuthPrincipal(
            kind="api_key",
            identity="crm-bridge",
            roles=["crm_bridge"],
            auth_method="bearer",
            is_anonymous=False,
        )


def _allow_all_firewall() -> ActionFirewall:
    fw = ActionFirewall()
    fw.add_rule(
        FirewallRule(
            action_pattern="*",
            actor_pattern="*",
            decision=PolicyDecision.ALLOW.value,
            risk_threshold=RiskLevel.UNKNOWN,
            autonomy_required=0,
        )
    )
    return fw


def _build_runtime(
    *,
    with_memory: bool = True,
    with_permission: bool = True,
    with_policy_rule: bool = True,
    firewall: ActionFirewall | None = None,
) -> Runtime:
    runtime = Runtime(config=SeedConfig())
    runtime.register("authenticator", _BridgeAuthenticator())
    fw = firewall if firewall is not None else _allow_all_firewall()
    runtime.register("firewall", fw)

    role_reg = RoleRegistry()
    role_reg.register(Role(role_id="crm_bridge", name="CRM Bridge"))
    perm_reg = PermissionRegistry(roles=role_reg)
    if with_permission:
        perm_reg.grant("crm_bridge", "crm:dispatch")
    runtime.register("permission_registry", perm_reg)

    rules = PolicyRuleSet()
    if with_policy_rule:
        rules.add(
            PolicyRule(
                name="allow-crm-dispatch",
                action_pattern="crm:dispatch",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="crm:dispatch",
                decision=PolicyDecision.ALLOW,
            )
        )
    policy = PolicyEngine(rule_set=rules, permission_registry=perm_reg)
    runtime.register("policy_engine", policy)

    if with_memory:
        runtime.register_memory_store("default", InMemoryMemoryStore())
        runtime.register("memory_manager", MemoryManager(store=runtime.get_memory_store("default")))
    return runtime


def _signed_headers(body: bytes, *, org: str | None = ORG_A) -> dict[str, str]:
    ts = str(int(time.time()))
    signature = hmac_mod.new(
        SECRET.encode("utf-8"), body + ts.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Signature": signature,
        "X-Timestamp": ts,
        "Content-Type": "application/json",
    }
    if org is not None:
        headers["X-Org-Id"] = org
    return headers


def _post(client: TestClient, event: dict[str, Any], **header_overrides: Any) -> Any:
    body = json.dumps(event).encode("utf-8")
    headers = _signed_headers(body, **header_overrides)
    return client.post(
        "/crm/dispatch", content=body, headers=headers, timeout=30
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ALBRA_CRM_HMAC_SECRET", SECRET)
    runtime = _build_runtime()
    identity = AgentIdentity(name="albra-agent", version="1.0.0")
    agent = Agent(identity=identity, runtime=runtime)
    app = create_app(
        runtime,
        agents={identity.agent_id: agent},
        extra_extensions=[install_crm_dispatch],
    )
    with TestClient(app) as c:
        yield c


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------
# Happy path + T9 persistence + idempotency
# ---------------------------------------------------------------------


def test_dispatch_acknowledges_and_persists_org_scoped_memory(client: TestClient):
    response = _post(client, _base_event())
    assert response.status_code == 200
    ack = response.json()
    assert ack == {
        "accepted": True,
        "event_id": "evt_s44_0001",
        "approvalRequired": False,
    }
    # The turn landed in the ORGANIZATION bucket (T9), domain crm.
    runtime = client.app.state.ctx.runtime  # type: ignore[attr-defined]
    mgr: MemoryManager = runtime.get_memory_manager()
    records = _run(
        mgr.recall(agent_id=AGENT_A, org_id=ORG_A, domain="crm",
                   user_id="ct_s44_1", context_id="cv_s44_1")
    )
    assert len(records) == 1
    assert records[0].org_id == ORG_A
    assert records[0].content["event_id"] == "evt_s44_0001"
    assert records[0].source == "crm_bridge"
    # And a cross-tenant read attempt by org B is a hard violation.
    with pytest.raises(PermissionError):
        _run(mgr.recall(agent_id=AGENT_A, org_id=ORG_B, domain="crm"))


def test_dispatch_is_idempotent_per_org_conversation_event(client: TestClient):
    first = _post(client, _base_event())
    replay = _post(client, _base_event())
    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    # A different event_id is processed as a new turn.
    second = _post(client, _base_event(event_id="evt_s44_0002"))
    assert second.status_code == 200
    # Same event_id in another conversation is NOT deduped (per-conversation).
    other_conv = _post(
        client, _base_event(conversation={"id": "cv_s44_2", "channel": "whatsapp", "test": True})
    )
    assert other_conv.status_code == 200


def test_dispatch_same_event_id_other_org_is_not_deduped(client: TestClient):
    """Negative-isolation proof through HTTP: the idempotency ledger is
    org-scoped — org B replaying org A's event_id is a NEW event for B
    and can never collide with (or reveal) A's ledger entry."""
    first = _post(client, _base_event())
    assert first.status_code == 200
    event_b = _base_event()
    event_b["organization"] = {
        "id": ORG_B,
        "agentId": AGENT_B,
        "timezone": "America/Bogota",
        "locale": "es",
    }
    second = _post(client, event_b, org=ORG_B)
    assert second.status_code == 200
    assert second.json()["accepted"] is True


# ---------------------------------------------------------------------
# Authentication + HMAC
# ---------------------------------------------------------------------


def test_dispatch_requires_bearer(client: TestClient):
    body = json.dumps(_base_event()).encode("utf-8")
    headers = _signed_headers(body)
    del headers["Authorization"]
    response = client.post("/crm/dispatch", content=body, headers=headers, timeout=30)
    assert response.status_code == 401


def test_dispatch_rejects_bad_bearer(client: TestClient):
    body = json.dumps(_base_event()).encode("utf-8")
    headers = _signed_headers(body)
    headers["Authorization"] = "Bearer wrong-token"
    response = client.post("/crm/dispatch", content=body, headers=headers, timeout=30)
    assert response.status_code == 401


def test_dispatch_rejects_tampered_signature(client: TestClient):
    body = json.dumps(_base_event()).encode("utf-8")
    headers = _signed_headers(body)
    headers["X-Signature"] = "0" * 64
    response = client.post("/crm/dispatch", content=body, headers=headers, timeout=30)
    assert response.status_code == 401
    assert "detail" in response.json()  # auth layer keeps plain semantics


def test_dispatch_rejects_signature_from_wrong_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALBRA_CRM_HMAC_SECRET", SECRET)
    runtime = _build_runtime()
    app = create_app(runtime, extra_extensions=[install_crm_dispatch])
    with TestClient(app) as c:
        body = json.dumps(_base_event()).encode("utf-8")
        ts = str(int(time.time()))
        signature = hmac_mod.new(
            b"otro-secreto", body + ts.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        response = c.post(
            "/crm/dispatch",
            content=body,
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "X-Signature": signature,
                "X-Timestamp": ts,
                "X-Org-Id": ORG_A,
            },
            timeout=30,
        )
        assert response.status_code == 401


def test_dispatch_rejects_stale_timestamp(client: TestClient):
    body = json.dumps(_base_event()).encode("utf-8")
    ts = str(int(time.time()) - 400)  # outside the ±300 s window
    signature = hmac_mod.new(
        SECRET.encode("utf-8"), body + ts.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    response = client.post(
        "/crm/dispatch",
        content=body,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "X-Signature": signature,
            "X-Timestamp": ts,
            "X-Org-Id": ORG_A,
        },
        timeout=30,
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------
# Tenant identity (tenant_mismatch) + contract validation
# ---------------------------------------------------------------------


def test_dispatch_rejects_org_header_mismatch(client: TestClient):
    response = _post(client, _base_event(), org=ORG_B)
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "tenant_mismatch"


def test_dispatch_rejects_missing_org_header(client: TestClient):
    response = _post(client, _base_event(), org=None)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_mismatch"


def test_dispatch_rejects_cross_org_agent_id(client: TestClient):
    event = _base_event()
    event["organization"]["agentId"] = AGENT_B  # belongs to org B
    response = _post(client, event)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_mismatch"


def test_dispatch_rejects_malformed_org_id(client: TestClient):
    event = _base_event()
    event["organization"]["id"] = "not-an-org"
    event["organization"]["agentId"] = "org:not-an-org#ventas"
    response = _post(client, event, org="not-an-org")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_dispatch_rejects_invalid_channel(client: TestClient):
    event = _base_event()
    event["conversation"]["channel"] = "pigeon"
    response = _post(client, event)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    # Sanitized details: no submitted values echoed back.
    assert all("pigeon" not in json.dumps(item) for item in error.get("details", []))


def test_dispatch_rejects_empty_message_ids(client: TestClient):
    event = _base_event(messageIds=[])
    response = _post(client, event)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_dispatch_rejects_wrong_event_name(client: TestClient):
    response = _post(client, _base_event(event="conversation.turn.other"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------
# Fail-closed configuration
# ---------------------------------------------------------------------


def test_dispatch_is_503_without_hmac_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ALBRA_CRM_HMAC_SECRET", raising=False)
    runtime = _build_runtime()
    app = create_app(runtime, extra_extensions=[install_crm_dispatch])
    with TestClient(app) as c:
        body = json.dumps(_base_event()).encode("utf-8")
        response = c.post(
            "/crm/dispatch",
            content=body,
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=30,
        )
        assert response.status_code == 503


def test_dispatch_is_503_without_tenant_memory(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALBRA_CRM_HMAC_SECRET", SECRET)
    runtime = _build_runtime(with_memory=False)
    app = create_app(runtime, extra_extensions=[install_crm_dispatch])
    with TestClient(app) as c:
        response = _post(c, _base_event())
        assert response.status_code == 503
        assert "memory" in response.json()["detail"]


def test_dispatch_is_503_without_security_perimeter(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALBRA_CRM_HMAC_SECRET", SECRET)
    runtime = _build_runtime()
    runtime._components.pop("firewall", None)
    runtime._components.pop("policy_engine", None)
    app = create_app(runtime, extra_extensions=[install_crm_dispatch])
    with TestClient(app) as c:
        response = _post(c, _base_event())
        assert response.status_code == 503
        assert "perimeter" in response.json()["detail"]


# ---------------------------------------------------------------------
# RBAC (execution policies)
# ---------------------------------------------------------------------


def test_dispatch_403_without_policy_rule(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALBRA_CRM_HMAC_SECRET", SECRET)
    runtime = _build_runtime(with_policy_rule=False)
    app = create_app(runtime, extra_extensions=[install_crm_dispatch])
    with TestClient(app) as c:
        response = _post(c, _base_event())
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "unauthorized_scope"


def test_dispatch_403_without_role_permission(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALBRA_CRM_HMAC_SECRET", SECRET)
    runtime = _build_runtime(with_permission=False)
    app = create_app(runtime, extra_extensions=[install_crm_dispatch])
    with TestClient(app) as c:
        response = _post(c, _base_event())
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "unauthorized_scope"


# ---------------------------------------------------------------------
# Firewall decisions
# ---------------------------------------------------------------------


def test_firewall_require_hitl_returns_202_and_persists_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ALBRA_CRM_HMAC_SECRET", SECRET)
    fw = ActionFirewall()
    fw.add_rule(
        FirewallRule(
            action_pattern="crm:dispatch",
            actor_pattern="*",
            decision=PolicyDecision.REQUIRE_HITL.value,
            risk_threshold=RiskLevel.UNKNOWN,
            autonomy_required=0,
        )
    )
    runtime = _build_runtime(firewall=fw)
    app = create_app(runtime, extra_extensions=[install_crm_dispatch])
    with TestClient(app) as c:
        response = _post(c, _base_event())
        assert response.status_code == 202
        error = response.json()["error"]
        assert error["code"] == "approval_required"
        assert error["details"]["event_id"] == "evt_s44_0001"
        # The turn is NOT persisted while it awaits the human decision.
        mgr: MemoryManager = c.app.state.ctx.runtime.get_memory_manager()  # type: ignore[attr-defined]
        records = _run(mgr.recall(agent_id=AGENT_A, org_id=ORG_A, domain="crm"))
        assert records == []


def test_firewall_deny_returns_403_firewall_denied(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALBRA_CRM_HMAC_SECRET", SECRET)
    fw = ActionFirewall()
    fw.add_rule(
        FirewallRule(
            action_pattern="crm:dispatch",
            actor_pattern="*",
            decision=PolicyDecision.DENY.value,
            risk_threshold=RiskLevel.UNKNOWN,
            autonomy_required=0,
        )
    )
    runtime = _build_runtime(firewall=fw)
    app = create_app(runtime, extra_extensions=[install_crm_dispatch])
    with TestClient(app) as c:
        response = _post(c, _base_event())
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "firewall_denied"


def test_firewall_no_matching_rule_is_default_deny(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALBRA_CRM_HMAC_SECRET", SECRET)
    fw = ActionFirewall()
    fw.add_rule(
        FirewallRule(
            action_pattern="other:action",
            actor_pattern="*",
            decision=PolicyDecision.ALLOW.value,
            risk_threshold=RiskLevel.UNKNOWN,
            autonomy_required=0,
        )
    )
    runtime = _build_runtime(firewall=fw)
    app = create_app(runtime, extra_extensions=[install_crm_dispatch])
    with TestClient(app) as c:
        response = _post(c, _base_event())
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "no_matching_rule"


# ---------------------------------------------------------------------
# Rate limit per organization
# ---------------------------------------------------------------------


def test_rate_limit_per_organization(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALBRA_CRM_HMAC_SECRET", SECRET)
    monkeypatch.setenv("ALBRA_CRM_DISPATCH_RATE_PER_MINUTE", "2")
    runtime = _build_runtime()
    app = create_app(runtime, extra_extensions=[install_crm_dispatch])
    with TestClient(app) as c:
        assert _post(c, _base_event(event_id="evt_r1")).status_code == 200
        assert _post(c, _base_event(event_id="evt_r2")).status_code == 200
        third = _post(c, _base_event(event_id="evt_r3"))
        assert third.status_code == 429
        error = third.json()["error"]
        assert error["code"] == "rate_limited"
        assert "Retry-After" in third.headers
        # Org B has its own bucket and is not throttled by org A's burst.
        event_b = _base_event(event_id="evt_r1")
        event_b["organization"] = {
            "id": ORG_B,
            "agentId": AGENT_B,
            "timezone": "America/Bogota",
            "locale": "es",
        }
        assert _post(c, event_b, org=ORG_B).status_code == 200


def test_dispatch_endpoint_listed_in_openapi(client: TestClient):
    paths = client.get("/openapi.json", timeout=30).json()["paths"]
    assert "/crm/dispatch" in paths
