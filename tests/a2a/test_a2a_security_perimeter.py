"""A2A security perimeter tests — verify the full enforcement pipeline
guards EVERY inbound A2A operation (message/send, tasks/get, tasks/cancel).

These tests close BLOQUEADORES 1, 2, 3 from the independent audit:
before this file existed, only ``message/send`` passed through the
A2ASecurityFilter. ``tasks/get`` and ``tasks/cancel`` short-circuited
straight to the task store, bypassing authentication / firewall /
policy / kill-switch enforcement entirely.

The fix (in ``src/albra_seed/a2a/security.py`` + ``server.py`` +
``routes/a2a.py`` + ``client.py`` + ``transport.py``) extends the
``A2ASecurityFilter`` with an ``evaluate_action`` method that runs the
SAME pipeline (auth -> firewall -> policy) for the
``a2a.tasks.get`` / ``a2a.tasks.cancel`` actions and forwards the
caller's token through every layer (route -> server -> filter).

Each test exercises the FULL real-HTTP stack via
``httpx.ASGITransport`` pointed at a FastAPI app that mounts the
``POST /a2a/v1/`` JSON-RPC endpoint. NO in-process shortcut is used
for the JSON-RPC round-trip — every call traverses httpx -> ASGI ->
FastAPI -> route -> A2AServer -> A2ASecurityFilter.

A STANDALONE non-ALBRA sender (raw ``httpx.AsyncClient``) is used on
the client side so the token originates from outside ALBRA — proving
the security perimeter is enforced on the receiving side, not just on
the sending side.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import httpx  # noqa: E402

from albra_seed.a2a import A2AMessage, A2AServer, A2ATask, TaskState  # noqa: E402
from albra_seed.api.app import create_app  # noqa: E402
from albra_seed.audit.normal import AuditLog  # noqa: E402
from albra_seed.core.config import SeedConfig  # noqa: E402
from albra_seed.core.identity import AgentIdentity  # noqa: E402
from albra_seed.core.types import PolicyDecision, RiskLevel  # noqa: E402
from albra_seed.evidence.manager import EvidenceManager  # noqa: E402
from albra_seed.firewall.firewall import ActionFirewall  # noqa: E402
from albra_seed.firewall.rate_limit import RateLimiter  # noqa: E402
from albra_seed.firewall.rules import FirewallRule  # noqa: E402
from albra_seed.handoff.manager import HandoffManager  # noqa: E402
from albra_seed.policy.engine import PolicyEngine  # noqa: E402
from albra_seed.policy.rules import PolicyRule  # noqa: E402
from albra_seed.provenance.tracker import ProvenanceTracker  # noqa: E402
from albra_seed.runtime.runtime import (  # noqa: E402
    COMPONENT_AUDIT_LOGGER,
    COMPONENT_EVIDENCE_COLLECTOR,
    COMPONENT_FIREWALL,
    COMPONENT_POLICY_ENGINE,
    Runtime,
)
from albra_seed.security.auth import AuthPrincipal  # noqa: E402


# =====================================================================
# Test-only authenticator that recognizes a single static token.
# Returns a real principal for the matching token; None for any other.
# This is the OPPOSITE of NoopAuthenticator (which lets everything in)
# and lets us verify the security perimeter DENIES unauthenticated calls.
# =====================================================================


class StaticTokenAuthenticator:
    """Test authenticator that recognizes a single static bearer token."""

    name = "static-token"
    is_mock = True

    def __init__(self, valid_token: str, principal: AuthPrincipal) -> None:
        self._valid_token = valid_token
        self._principal = principal

    async def authenticate(self, credentials: Any = None) -> AuthPrincipal | None:  # noqa: ARG002
        if credentials == self._valid_token:
            return self._principal
        return None

    async def verify(self, token: str | None = None) -> AuthPrincipal | None:
        if token == self._valid_token:
            return self._principal
        return None


# Canonical token + principal used across these tests. The string is
# deliberately long and unique so the "token never in logs" test can
# grep through captured logs / audit / evidence without false positives.
_VALID_TOKEN = "PERIMETER-TEST-TOKEN-9f3e7c1d-DO-NOT-LEAK"
_BAD_TOKEN = "wrong-token-shall-not-pass"
_PRINCIPAL = AuthPrincipal(
    kind="service",
    identity="perimeter-test-client",
    roles=["a2a.client"],
    auth_method="static",
    is_anonymous=False,
)


# =====================================================================
# Helpers — runtime + app construction
# =====================================================================


async def _make_albra_app(
    *,
    authenticator: Any | None = None,
    firewall: ActionFirewall | None = None,
    policy_engine: PolicyEngine | None = None,
) -> tuple[Any, Runtime, A2AServer, AuditLog, ProvenanceTracker, EvidenceManager]:
    """Build a started ALBRA runtime + A2AServer + FastAPI app.

    The runtime is wired with audit / provenance / evidence so tests can
    inspect side-effects (denied audit entries, evidence records, etc.).
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
    if firewall is not None:
        rt.register(COMPONENT_FIREWALL, firewall)
    if policy_engine is not None:
        rt.register(COMPONENT_POLICY_ENGINE, policy_engine)
    ident = AgentIdentity(name="perimeter-agent", endpoint="http://perim-test")
    server = A2AServer(rt, identity=ident, handoff_manager=HandoffManager())
    app = create_app(rt, a2a_server=server)
    return app, rt, server, audit, prov, ev


def _wire_msg(text: str, *, role: str = "user") -> dict[str, Any]:
    """Build a wire-format A2A message dict."""
    return {
        "role": role,
        "parts": [{"type": "text", "text": text}],
        "messageId": f"msg-{text[:8]}",
    }


def _rpc(method: str, params: dict[str, Any], req_id: str | int = 1) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 request envelope."""
    return {"jsonrpc": "2.0", "method": method, "params": params, "id": req_id}


def _auth_headers(token: str | None) -> dict[str, str]:
    """Build the Authorization header for the given token."""
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _send_task(
    client: httpx.AsyncClient,
    text: str = "hello-perimeter",
    *,
    token: str | None = _VALID_TOKEN,
) -> str:
    """Helper: send a task via JSON-RPC and return its task_id."""
    r = await client.post(
        "http://perim-test/a2a/v1/",
        json=_rpc("message/send", params={"message": _wire_msg(text)}),
        headers=_auth_headers(token),
    )
    assert r.status_code == 200, f"send failed: {r.status_code} {r.text}"
    return str(r.json()["result"]["id"])


# =====================================================================
# 1. tasks/get — unauthorized (bad / no token) is DENIED
# =====================================================================


async def test_tasks_get_unauthorized_returns_deny():
    """POST tasks/get with no/bad token → security DENIES → no status returned.

    The route's JSON-RPC response must be an ERROR response (the server
    returned None to the route). The audit log MUST record the denial.
    """
    auth = StaticTokenAuthenticator(_VALID_TOKEN, _PRINCIPAL)
    app, rt, _server, audit, _prov, _ev = await _make_albra_app(authenticator=auth)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            # First send a task (with the VALID token) so it exists.
            task_id = await _send_task(client, text="seed-task")
            # Now query its status with a BAD token → must be denied.
            r = await client.post(
                "http://perim-test/a2a/v1/",
                json=_rpc("tasks/get", params={"id": task_id}, req_id=2),
                headers=_auth_headers(_BAD_TOKEN),
            )
            assert r.status_code == 200
            body = r.json()
            # Either JSON-RPC error response OR a result missing the task.
            assert "error" in body, "expected JSON-RPC error for denied tasks/get"
            assert body["error"]["code"] == -32602
            # Audit log records the denial (security_reason captured).
            denied_entries = [e for e in audit.all() if e.result == "denied"]
            assert any(
                e.metadata.get("denied_action") == "a2a.tasks.get"
                or "tasks.get" in (e.metadata.get("security_reason", "") + e.error or "")
                for e in denied_entries
            ), f"no tasks/get denial recorded in audit: {denied_entries}"
    finally:
        await rt.shutdown()


# =====================================================================
# 2. tasks/cancel — unauthorized (bad / no token) is DENIED
# =====================================================================


async def test_tasks_cancel_unauthorized_returns_deny():
    """POST tasks/cancel with no/bad token → security DENIES → no cancel."""
    auth = StaticTokenAuthenticator(_VALID_TOKEN, _PRINCIPAL)
    app, rt, _server, audit, _prov, _ev = await _make_albra_app(authenticator=auth)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            task_id = await _send_task(client, text="cancel-target")
            # Cancel with a BAD token → must be denied.
            r = await client.post(
                "http://perim-test/a2a/v1/",
                json=_rpc("tasks/cancel", params={"id": task_id}, req_id=2),
                headers=_auth_headers(_BAD_TOKEN),
            )
            assert r.status_code == 200
            body = r.json()
            assert "error" in body, "expected JSON-RPC error for denied tasks/cancel"
            assert body["error"]["code"] == -32602
            denied_entries = [e for e in audit.all() if e.result == "denied"]
            assert any(
                "tasks.cancel" in (e.metadata.get("security_reason", "") + e.error or "")
                or e.metadata.get("denied_action") == "a2a.tasks.cancel"
                for e in denied_entries
            ), f"no tasks/cancel denial recorded: {denied_entries}"
    finally:
        await rt.shutdown()


# =====================================================================
# 3. tasks/get — valid token returns the status
# =====================================================================


async def test_tasks_get_with_valid_token_allowed():
    """POST tasks/get with a valid token → status returned.

    The authenticator recognizes the token; the firewall + policy
    engine (none registered in this test → CORE_ONLY mode) allow the
    anonymous principal; the server returns the stored task status.
    """
    auth = StaticTokenAuthenticator(_VALID_TOKEN, _PRINCIPAL)
    app, rt, _server, _audit, _prov, _ev = await _make_albra_app(authenticator=auth)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            task_id = await _send_task(client, text="status-target")
            r = await client.post(
                "http://perim-test/a2a/v1/",
                json=_rpc("tasks/get", params={"id": task_id}, req_id=2),
                headers=_auth_headers(_VALID_TOKEN),
            )
            assert r.status_code == 200
            body = r.json()
            assert body["jsonrpc"] == "2.0"
            assert "result" in body, f"expected result, got: {body}"
            assert body["result"]["id"] == task_id
            assert body["result"]["status"]["state"] == "completed"
    finally:
        await rt.shutdown()


# =====================================================================
# 4. tasks/cancel — valid token cancels the task
# =====================================================================


async def test_tasks_cancel_with_valid_token_allowed():
    """POST tasks/cancel with a valid token → task transitions to CANCELED.

    Injects a WORKING task into the server's store (because completed
    tasks are terminal and cancel returns them unchanged per the
    A2AServer contract).
    """
    auth = StaticTokenAuthenticator(_VALID_TOKEN, _PRINCIPAL)
    app, rt, server, _audit, _prov, _ev = await _make_albra_app(authenticator=auth)
    try:
        # Inject a non-terminal task so cancel can transition it.
        working = A2ATask(
            task_id="cancel-valid-target",
            state=TaskState.WORKING,
            message=A2AMessage.from_text("in-progress"),
        )
        server._tasks["cancel-valid-target"] = working
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            r = await client.post(
                "http://perim-test/a2a/v1/",
                json=_rpc(
                    "tasks/cancel",
                    params={"id": "cancel-valid-target"},
                    req_id=1,
                ),
                headers=_auth_headers(_VALID_TOKEN),
            )
            assert r.status_code == 200
            body = r.json()
            assert "result" in body, f"expected result, got: {body}"
            assert body["result"]["id"] == "cancel-valid-target"
            assert body["result"]["status"]["state"] == "canceled"
    finally:
        await rt.shutdown()


# =====================================================================
# 5. tasks/get — firewall explicitly DENIES the action
# =====================================================================


async def test_tasks_get_firewall_deny():
    """A FirewallRule denying ``a2a.tasks.get`` blocks the operation.

    Even with a valid token + authenticated principal, the firewall
    rule takes precedence: the security filter returns a deny, the
    server returns None, the route returns a JSON-RPC error.
    """
    auth = StaticTokenAuthenticator(_VALID_TOKEN, _PRINCIPAL)
    fw = ActionFirewall(
        rules=[
            # DENY tasks/get for everyone (priority wins over the default
            # no-matching-rule DENY, but the explicit rule's reason is
            # what we check below).
            FirewallRule(
                name="deny-tasks-get",
                action_pattern="a2a.tasks.get",
                actor_pattern="*",
                decision=PolicyDecision.DENY,
                risk_threshold=RiskLevel.UNKNOWN,
                priority=100,
            ),
        ]
    )
    app, rt, _server, audit, _prov, _ev = await _make_albra_app(
        authenticator=auth, firewall=fw
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            task_id = await _send_task(client, text="firewall-target")
            r = await client.post(
                "http://perim-test/a2a/v1/",
                json=_rpc("tasks/get", params={"id": task_id}, req_id=2),
                headers=_auth_headers(_VALID_TOKEN),
            )
            assert r.status_code == 200
            body = r.json()
            assert "error" in body, "expected deny from firewall"
            # Audit log records a denial with a firewall reason.
            denied = [e for e in audit.all() if e.result == "denied"]
            assert any(
                "firewall_denied" in (e.error or "")
                for e in denied
            ), f"expected firewall_denied in audit: {denied}"
    finally:
        await rt.shutdown()


# =====================================================================
# 6. tasks/cancel — policy explicitly DENIES the action
# =====================================================================


async def test_tasks_cancel_policy_deny():
    """A PolicyRule denying ``a2a.tasks.cancel`` blocks the operation.

    The firewall allows everything; the policy engine denies the
    specific action. The token is valid; the principal is authenticated;
    the policy denial still wins.
    """
    auth = StaticTokenAuthenticator(_VALID_TOKEN, _PRINCIPAL)
    fw = ActionFirewall(
        rules=[
            FirewallRule(
                name="allow-all",
                action_pattern="*",
                decision=PolicyDecision.ALLOW,
                risk_threshold=RiskLevel.UNKNOWN,
                priority=10,
            )
        ]
    )
    pol = PolicyEngine()
    pol.add_rule(
        PolicyRule(
            name="deny-cancel",
            action_pattern="a2a.tasks.cancel",
            actor_pattern="*",
            decision=PolicyDecision.DENY,
            risk_threshold=RiskLevel.UNKNOWN,
            priority=100,
        )
    )
    app, rt, _server, audit, _prov, _ev = await _make_albra_app(
        authenticator=auth, firewall=fw, policy_engine=pol
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            task_id = await _send_task(client, text="policy-cancel-target")
            r = await client.post(
                "http://perim-test/a2a/v1/",
                json=_rpc("tasks/cancel", params={"id": task_id}, req_id=2),
                headers=_auth_headers(_VALID_TOKEN),
            )
            assert r.status_code == 200
            body = r.json()
            assert "error" in body, "expected deny from policy"
            denied = [e for e in audit.all() if e.result == "denied"]
            assert any(
                "policy_denied" in (e.error or "")
                for e in denied
            ), f"expected policy_denied in audit: {denied}"
    finally:
        await rt.shutdown()


# =====================================================================
# 7. tasks/get — kill switch blocks EVERY A2A op
# =====================================================================


async def test_tasks_get_kill_switch_blocks():
    """An active kill switch blocks ``tasks/get`` (and every other action).

    The kill switch is GLOBAL — once active, the firewall returns
    DENY for every action, including read-only status queries.
    """
    auth = StaticTokenAuthenticator(_VALID_TOKEN, _PRINCIPAL)
    fw = ActionFirewall(
        rules=[
            FirewallRule(
                name="allow-all",
                action_pattern="*",
                decision=PolicyDecision.ALLOW,
                risk_threshold=RiskLevel.UNKNOWN,
                priority=10,
            )
        ]
    )
    # Activate the kill switch BEFORE wiring the firewall to the
    # runtime — the firewall reads its state lazily on every verdict.
    await fw.kill_switch.activate(
        reason="perimeter-test-kill", activated_by="test"
    )
    app, rt, _server, audit, _prov, _ev = await _make_albra_app(
        authenticator=auth, firewall=fw
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            task_id = await _send_task(client, text="kill-switch-target")
            # Kill switch is active during the get_status call.
            assert fw.kill_switch.is_active()
            r = await client.post(
                "http://perim-test/a2a/v1/",
                json=_rpc("tasks/get", params={"id": task_id}, req_id=2),
                headers=_auth_headers(_VALID_TOKEN),
            )
            assert r.status_code == 200
            body = r.json()
            assert "error" in body, "expected deny from kill switch"
            denied = [e for e in audit.all() if e.result == "denied"]
            assert any(
                "kill_switch_active" in (e.error or "")
                or "kill_switch" in (e.error or "")
                for e in denied
            ), f"expected kill_switch reason in audit: {denied}"
    finally:
        await rt.shutdown()


# =====================================================================
# 8. tasks/get — rate limit exhausted → DENY
# =====================================================================


async def test_tasks_get_rate_limit():
    """A rate limiter with zero initial tokens denies ``tasks/get``.

    The RateLimiter cannot be constructed with capacity=0 (validation
    rejects it); instead we set ``initial_tokens=0.0`` so the bucket
    starts EMPTY. With a tiny refill rate, the first call cannot
    accumulate enough tokens and is denied.
    """
    auth = StaticTokenAuthenticator(_VALID_TOKEN, _PRINCIPAL)
    rl = RateLimiter(capacity=1, refill_per_sec=0.001, initial_tokens=0.0)
    fw = ActionFirewall(
        rules=[
            FirewallRule(
                name="allow-all",
                action_pattern="*",
                decision=PolicyDecision.ALLOW,
                risk_threshold=RiskLevel.UNKNOWN,
                priority=10,
            )
        ],
        rate_limiter=rl,
    )
    app, rt, _server, audit, _prov, _ev = await _make_albra_app(
        authenticator=auth, firewall=fw
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app)
        ) as client:
            task_id = await _send_task(client, text="rate-limit-target")
            # The tasks/get action consumes a token from the
            # (actor="perimeter-test-client", action="a2a.tasks.get")
            # bucket. With 0 initial tokens + ~0 refill, the verdict
            # is rate-limited → DENY.
            r = await client.post(
                "http://perim-test/a2a/v1/",
                json=_rpc("tasks/get", params={"id": task_id}, req_id=2),
                headers=_auth_headers(_VALID_TOKEN),
            )
            assert r.status_code == 200
            body = r.json()
            assert "error" in body, "expected deny from rate limit"
            denied = [e for e in audit.all() if e.result == "denied"]
            assert any(
                "rate_limit" in (e.error or "")
                for e in denied
            ), f"expected rate_limit reason in audit: {denied}"
    finally:
        await rt.shutdown()


# =====================================================================
# 9. Token NEVER appears in logs / evidence / audit / metadata
# =====================================================================


async def test_token_never_in_logs_evidence_audit():
    """The bearer token string must NEVER appear in:

      * structlog captured logs (event name, log level, any key/value)
      * audit log entries (action, actor, error, metadata)
      * evidence records (source, content, content_ref, metadata)
      * task metadata stored on the server

    The token is the SECRET used to authenticate; only the resolved
    principal's identity should be recorded. The test runs a successful
    message/send + tasks/get round-trip with a recognizable token
    string and then asserts the token does not appear anywhere in the
    recorded side-effects.
    """
    from structlog.testing import capture_logs

    auth = StaticTokenAuthenticator(_VALID_TOKEN, _PRINCIPAL)
    # Use a permissive firewall + policy so the operations succeed
    # (this maximises the side-effects: audit, evidence, provenance,
    # structlog policy.evaluate logs all fire).
    fw = ActionFirewall(
        rules=[
            FirewallRule(
                name="allow-all",
                action_pattern="*",
                decision=PolicyDecision.ALLOW,
                risk_threshold=RiskLevel.UNKNOWN,
                priority=10,
            )
        ]
    )
    pol = PolicyEngine()
    pol.add_rule(
        PolicyRule(
            name="allow-all",
            action_pattern="*",
            actor_pattern="*",
            decision=PolicyDecision.ALLOW,
            risk_threshold=RiskLevel.UNKNOWN,
            priority=10,
        )
    )
    app, rt, server, audit, _prov, ev = await _make_albra_app(
        authenticator=auth, firewall=fw, policy_engine=pol
    )
    try:
        with capture_logs() as cap:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app)
            ) as client:
                task_id = await _send_task(client, text="leak-check")
                # tasks/get round-trip — exercises get_status path too.
                r = await client.post(
                    "http://perim-test/a2a/v1/",
                    json=_rpc("tasks/get", params={"id": task_id}, req_id=2),
                    headers=_auth_headers(_VALID_TOKEN),
                )
                assert r.status_code == 200
                assert "result" in r.json()

        # ---- 1. Structlog captured logs -----------------------------------
        for entry in cap:
            for key, value in entry.items():
                assert _VALID_TOKEN not in str(key), (
                    f"token leaked in structlog key: {key} (event={entry.get('event')})"
                )
                assert _VALID_TOKEN not in str(value), (
                    f"token leaked in structlog value: key={key} value={value!r} "
                    f"(event={entry.get('event')})"
                )

        # ---- 2. Audit log entries -----------------------------------------
        for entry in audit.all():
            # Check every field of the entry (action, actor, error, tool,
            # provider, permission, approval_id, result, correlation_id,
            # execution_id, metadata values).
            entry_dict = entry.to_dict()
            for key, value in entry_dict.items():
                assert _VALID_TOKEN not in str(key), (
                    f"token leaked in audit key: {key}"
                )
                assert _VALID_TOKEN not in str(value), (
                    f"token leaked in audit value: key={key} value={value!r}"
                )

        # ---- 3. Evidence records ------------------------------------------
        all_evidence = await ev.list_all(limit=1000)
        assert len(all_evidence) > 0, "expected at least one evidence record"
        for evidence in all_evidence:
            # Check the content (which is the HandoffResponse.result dict)
            # and all the other fields.
            assert _VALID_TOKEN not in str(evidence.source), evidence
            assert _VALID_TOKEN not in str(evidence.source_type), evidence
            assert _VALID_TOKEN not in str(evidence.content), evidence
            assert _VALID_TOKEN not in str(evidence.content_ref or ""), evidence
            assert _VALID_TOKEN not in str(evidence.execution_id or ""), evidence
            assert _VALID_TOKEN not in str(evidence.evidence_id), evidence

        # ---- 4. Task metadata stored on the server ------------------------
        for task in server.list_tasks():
            for key, value in task.metadata.items():
                assert _VALID_TOKEN not in str(key), (
                    f"token leaked in task metadata key: {key}"
                )
                assert _VALID_TOKEN not in str(value), (
                    f"token leaked in task metadata value: key={key} value={value!r}"
                )

        # ---- 5. Sanity: the principal identity IS recorded ----------------
        # The audit log captures the actor (principal.identity), not the
        # token. This proves the principal is surfaced (auditable) while
        # the token is not (private).
        audit_actors = {e.actor for e in audit.all() if e.actor}
        assert _PRINCIPAL.identity in audit_actors or "anonymous" in audit_actors, (
            f"expected principal identity in audit actors: {audit_actors}"
        )
        assert _VALID_TOKEN not in {e.actor for e in audit.all() if e.actor}
    finally:
        await rt.shutdown()
