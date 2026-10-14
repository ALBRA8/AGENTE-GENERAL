"""Security regression tests for the public HITL approval API."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from albra_seed.api.app import create_app
from albra_seed.core.config import SeedConfig
from albra_seed.core.types import PolicyDecision, RiskLevel
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule, PolicyRuleSet
from albra_seed.runtime.runtime import Runtime
from albra_seed.security.auth import AuthPrincipal


class _TestAuthenticator:
    async def verify(self, token: str | None):
        if token != "valid-test-token":
            return None
        return AuthPrincipal(
            kind="user",
            identity="reviewer-1",
            roles=["reviewer"],
            auth_method="test",
            is_anonymous=False,
        )


def _client(*, add_permissions: bool = False):
    runtime = Runtime(config=SeedConfig())
    runtime.register("authenticator", _TestAuthenticator())
    rules = PolicyRuleSet()
    if add_permissions:
        rules.add(PolicyRule(
            name="list approvals",
            action_pattern="approval:list",
            actor_pattern="reviewer-1",
            risk_threshold=RiskLevel.UNKNOWN,
            required_permission="approval:list",
            decision=PolicyDecision.ALLOW,
        ))
        rules.add(PolicyRule(
            name="decide approvals",
            action_pattern="approval:decide",
            actor_pattern="reviewer-1",
            risk_threshold=RiskLevel.UNKNOWN,
            required_permission="approval:decide",
            decision=PolicyDecision.ALLOW,
        ))
    runtime.register("policy_engine", PolicyEngine(rule_set=rules))
    from albra_seed.approvals.engine import ApprovalEngine
    engine = ApprovalEngine()
    runtime.register("approval_engine", engine)
    client = TestClient(create_app(runtime))
    return client, engine


def test_approval_api_requires_bearer_authentication():
    client, _ = _client(add_permissions=True)
    response = client.get("/api/v1/approvals/pending")
    assert response.status_code == 401


def test_approval_api_rejects_invalid_or_anonymous_credentials():
    client, _ = _client(add_permissions=True)
    assert client.get(
        "/api/v1/approvals/pending",
        headers={"Authorization": "Bearer invalid"},
    ).status_code == 401


def test_approval_api_requires_explicit_action_specific_permission():
    client, _ = _client(add_permissions=False)
    response = client.get(
        "/api/v1/approvals/pending",
        headers={"Authorization": "Bearer valid-test-token"},
    )
    assert response.status_code == 403


def test_approval_decision_records_verified_identity_and_rejects_replay():
    client, engine = _client(add_permissions=True)
    import asyncio
    pending = asyncio.run(engine.request("send_message"))
    headers = {"Authorization": "Bearer valid-test-token"}
    response = client.post(
        f"/api/v1/approvals/{pending.approval_id}/decision",
        headers=headers,
        json={"decision": "approve"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["decided_by"] == "reviewer-1"

    replay = client.post(
        f"/api/v1/approvals/{pending.approval_id}/decision",
        headers=headers,
        json={"decision": "approve"},
    )
    assert replay.status_code == 200
    assert replay.json()["status"] == "approved"
    assert replay.json()["reason"] == "not_pending_or_missing"


def test_approval_decision_rejects_invalid_payload():
    client, engine = _client(add_permissions=True)
    import asyncio
    pending = asyncio.run(engine.request("send_message"))
    response = client.post(
        f"/api/v1/approvals/{pending.approval_id}/decision",
        headers={"Authorization": "Bearer valid-test-token"},
        json={"decision": "approve; DROP"},
    )
    assert response.status_code == 400
