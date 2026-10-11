"""A2A (Agent2Agent) integration tests.

These tests verify the A2A adapter layer behaves correctly:

  * AgentCard is DERIVED from the manifest (single source of truth).
  * A2AAdapter TRANSLATES between A2ATask and HandoffRequest/Response.
  * A2AServer enforces the full security stack (auth, firewall, policy,
    HITL) — NEVER bypasses.
  * InProcessTransport works in CORE_ONLY mode (no httpx needed).
  * Audit / provenance / evidence are recorded for every operation.
  * Idempotency: same task_id submitted twice → no re-execution.

All tests run in CORE_ONLY mode (default SeedConfig — no API keys,
no network). The full A2A flow works end-to-end using only the
InProcessTransport + a default no-op handler.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest

from albra_seed.a2a import (
    A2AAdapter,
    A2AAgentRecord,
    A2ACardSkill,
    A2AClient,
    A2AMessage,
    A2ARegistry,
    A2AServer,
    A2ATask,
    A2ATaskStatus,
    AgentCard,
    HTTPTransport,
    InProcessTransport,
    TaskState,
    manifest_to_card,
)
from albra_seed.approvals.engine import ApprovalEngine
from albra_seed.audit.normal import AuditLog
from albra_seed.core.config import SeedConfig
from albra_seed.core.identity import AgentIdentity, AgentManifest
from albra_seed.core.types import (
    AutonomyLevel,
    PolicyDecision,
    RiskLevel,
)
from albra_seed.evidence.manager import EvidenceManager
from albra_seed.firewall.firewall import ActionFirewall
from albra_seed.firewall.rules import FirewallRule
from albra_seed.handoff.contract import HandoffRequest, HandoffResponse, HandoffStatus
from albra_seed.handoff.manager import HandoffManager
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule
from albra_seed.provenance.tracker import ProvenanceTracker
from albra_seed.runtime.runtime import (
    COMPONENT_APPROVAL_ENGINE,
    COMPONENT_AUDIT_LOGGER,
    COMPONENT_EVIDENCE_COLLECTOR,
    COMPONENT_FIREWALL,
    COMPONENT_POLICY_ENGINE,
    Runtime,
)
from albra_seed.security.auth import Authenticator, AuthPrincipal

# =====================================================================
# Helpers
# =====================================================================


def _identity(name: str = "a2a-agent", endpoint: str = "in-process://a2a") -> AgentIdentity:
    return AgentIdentity(
        name=name,
        version="1.2.3",
        capabilities=["a2a.task.receive"],
        skills=["echo", "summarize"],
        interfaces=["text"],
        endpoint=endpoint,
    )


async def _make_runtime(*, with_security: bool = False) -> tuple[Runtime, AuditLog, ProvenanceTracker, EvidenceManager]:
    """Build a CORE_ONLY runtime with optional security stack."""
    runtime = Runtime(SeedConfig())
    await runtime.startup()
    audit = AuditLog()
    prov = ProvenanceTracker()
    ev = EvidenceManager()
    runtime.register(COMPONENT_AUDIT_LOGGER, audit)
    runtime.register("provenance_tracker", prov)
    runtime.register(COMPONENT_EVIDENCE_COLLECTOR, ev)
    if with_security:
        runtime.register(COMPONENT_FIREWALL, ActionFirewall())
        runtime.register(COMPONENT_POLICY_ENGINE, PolicyEngine())
        runtime.register(COMPONENT_APPROVAL_ENGINE, ApprovalEngine())
    return runtime, audit, prov, ev


async def _make_server(
    runtime: Runtime,
    *,
    identity: AgentIdentity | None = None,
    handler=None,
) -> tuple[A2AServer, HandoffManager, InProcessTransport, A2AClient]:
    ident = identity or _identity()
    mgr = HandoffManager()
    server = A2AServer(runtime, identity=ident, handoff_manager=mgr, handler=handler)
    transport = InProcessTransport()
    transport.register(ident.endpoint, server)
    client = A2AClient(transport, requester="test-client")
    return server, mgr, transport, client


# =====================================================================
# 1. Agent Card generation from Manifest
# =====================================================================


def test_agent_card_generation_from_manifest():
    """manifest_to_card populates every AgentCard field from the manifest."""
    ident = _identity()
    manifest = AgentManifest(identity=ident)
    card = manifest_to_card(manifest, endpoint="https://a2a.example/card")

    assert isinstance(card, AgentCard)
    assert card.protocol_version == "1.0"
    assert card.name == ident.name
    assert card.version == ident.version
    assert card.endpoint == "https://a2a.example/card"
    assert set(ident.capabilities).issubset(set(card.capabilities))
    # Skills are derived from the manifest's identity.skills list.
    assert len(card.skills) == len(ident.skills)
    assert all(isinstance(s, A2ACardSkill) for s in card.skills)
    # Default input/output modes propagated from identity.interfaces.
    assert "text" in card.default_input_modes
    assert card.default_output_modes == ["text"]
    # Provider info carries seed/runtime versions.
    assert card.provider["seed_version"] == ident.seed_version
    assert card.provider["runtime_version"] == ident.runtime_version
    # Metadata carries the agent_id and manifest_version.
    assert card.metadata["agent_id"] == ident.agent_id


def test_manifest_to_card_mapping_preserves_identity():
    """agent_id, name, version propagate from manifest → card."""
    ident = AgentIdentity(name="custom-agent", version="9.9.9")
    manifest = AgentManifest(identity=ident)
    card = manifest_to_card(manifest, endpoint="in-process://custom")

    assert card.name == "custom-agent"
    assert card.version == "9.9.9"
    assert card.endpoint == "in-process://custom"
    assert card.metadata["agent_id"] == ident.agent_id


def test_a2a_card_endpoint_field():
    """The endpoint field is accessible and populated on the card."""
    ident = _identity()
    manifest = AgentManifest(identity=ident)
    card = manifest_to_card(manifest, endpoint="https://node.example/a2a")
    assert card.endpoint == "https://node.example/a2a"

    # When no endpoint is provided, falls back to identity.endpoint.
    card2 = manifest_to_card(manifest)
    assert card2.endpoint == ident.endpoint


# =====================================================================
# 2. A2AAdapter — translation only
# =====================================================================


def test_a2a_request_to_handoff_request_translation():
    """A2AAdapter.task_to_handoff_request maps every field correctly."""
    adapter = A2AAdapter()
    task = A2ATask(
        task_id="task-123",
        requester="agent-A",
        target="agent-B",
        message=A2AMessage.from_text("summarize this"),
        correlation_id="corr-1",
        parent_execution_id="exec-parent",
        metadata={"risk": "high"},
    )
    req = adapter.task_to_handoff_request(task)

    assert isinstance(req, HandoffRequest)
    assert req.execution_id == "task-123"
    assert req.requester == "agent-A"
    assert req.target == "agent-B"
    assert req.objective == "summarize this"
    assert req.correlation_id == "corr-1"
    assert req.parent_execution_id == "exec-parent"
    assert req.risk == RiskLevel.HIGH
    # A2A-specific metadata is preserved in the input payload.
    assert req.input["message"] is not None
    assert req.metadata["source"] == "a2a"


def test_handoff_response_to_a2a_response_translation():
    """HandoffResponse.status maps to A2ATask.state correctly."""
    adapter = A2AAdapter()
    task = A2ATask(
        task_id="task-1",
        requester="A",
        target="B",
        message=A2AMessage.from_text("hello"),
    )

    # succeeded → COMPLETED
    resp = HandoffResponse.success("task-1", result={"text": "hi"}, confidence=0.9)
    out = adapter.handoff_response_to_task(resp, task)
    assert out.state == TaskState.COMPLETED
    assert out.metadata["confidence"] == 0.9
    assert out.artifacts  # at least one artifact from the result

    # failed → FAILED
    fail = HandoffResponse.failure("task-1", errors=["bad input"])
    out_fail = adapter.handoff_response_to_task(fail, task)
    assert out_fail.state == TaskState.FAILED
    assert "bad input" in out_fail.metadata["errors"]

    # cancelled → CANCELED
    cancel = HandoffResponse(
        execution_id="task-1",
        status=HandoffStatus.CANCELLED,
        responder="B",
    )
    out_cancel = adapter.handoff_response_to_task(cancel, task)
    assert out_cancel.state == TaskState.CANCELED

    # timeout → FAILED (per design — A2A has no separate timeout state)
    timeout = HandoffResponse.timeout("task-1")
    out_timeout = adapter.handoff_response_to_task(timeout, task)
    assert out_timeout.state == TaskState.FAILED
    assert out_timeout.metadata["errors"]


# =====================================================================
# 3. A2AServer — task lifecycle
# =====================================================================


async def test_task_lifecycle_submitted_to_completed():
    """Submit a task → InProcess → handler runs → COMPLETED state."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        server, mgr, transport, client = await _make_server(runtime)
        task = await client.send_task(
            "in-process://a2a", message="compute the answer"
        )
        assert task.state == TaskState.COMPLETED
        assert task.is_terminal is True
        # The default handler echoes the objective back as the result.
        assert task.artifacts  # at least one artifact
        # Confidence is propagated into the task metadata.
        assert "confidence" in task.metadata
    finally:
        await runtime.shutdown()


async def test_task_status_query():
    """get_status returns the current state of a submitted task."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        server, mgr, transport, client = await _make_server(runtime)
        task = await client.send_task("in-process://a2a", message="hello")
        status = await client.get_status("in-process://a2a", task.task_id)

        assert isinstance(status, A2ATaskStatus)
        assert status.task_id == task.task_id
        assert status.state == TaskState.COMPLETED
        # Status carries a snapshot of artifacts + message.
        assert isinstance(status.artifacts, list)
        assert status.updated_at is not None
    finally:
        await runtime.shutdown()


async def test_task_cancellation():
    """Cancel a submitted task → state becomes CANCELED.

    A task that's already terminal (COMPLETED/FAILED) is returned
    unchanged by `cancel()`; we verify both branches:
      1. Cancelling a SUBMITTED task → CANCELED.
      2. Cancelling a terminal task → returned unchanged.
    """
    runtime, audit, prov, ev = await _make_runtime()
    try:
        server, mgr, transport, client = await _make_server(runtime)
        # Inject a SUBMITTED task directly into the server's store so
        # it has NOT been processed yet (the InProcessTransport
        # otherwise completes the task synchronously).
        submitted_task = A2ATask(
            task_id="cancel-target-1",
            requester="test-client",
            target="a2a-agent",
            message=A2AMessage.from_text("waiting for cancel"),
        )
        server._tasks[submitted_task.task_id] = submitted_task  # noqa: SLF001

        canceled = await client.cancel("in-process://a2a", submitted_task.task_id)
        assert canceled is not None
        assert canceled.state == TaskState.CANCELED
        assert canceled.metadata.get("canceled_at") is not None

        # Branch 2: cancelling an already-terminal task returns it
        # unchanged. Submit a normal task (which completes immediately).
        completed = await client.send_task("in-process://a2a", message="done")
        assert completed.state == TaskState.COMPLETED
        canceled_after_done = await client.cancel(
            "in-process://a2a", completed.task_id
        )
        assert canceled_after_done is not None
        assert canceled_after_done.state == TaskState.COMPLETED  # unchanged

        # Cancelling an unknown task returns None.
        assert await client.cancel("in-process://a2a", "does-not-exist") is None
    finally:
        await runtime.shutdown()


async def test_artifacts_propagation():
    """A handler returning a result dict surfaces as an A2AArtifact."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        async def custom_handler(req: HandoffRequest) -> HandoffResponse:
            return HandoffResponse.success(
                req.execution_id,
                result={"text": "computed-output"},
                confidence=0.95,
                responder=req.target,
            )

        server, mgr, transport, client = await _make_server(
            runtime, handler=custom_handler
        )
        task = await client.send_task("in-process://a2a", message="compute")

        assert task.state == TaskState.COMPLETED
        assert task.artifacts
        # The artifact should carry the result text.
        text_parts = [
            p.get("text")
            for a in task.artifacts
            for p in a.parts
            if p.get("type") == "text"
        ]
        assert any("computed-output" in t for t in text_parts if t)
        assert task.metadata["confidence"] == 0.95
    finally:
        await runtime.shutdown()


# =====================================================================
# 4. Cross-cutting: correlation_id, provenance, evidence, audit
# =====================================================================


async def test_correlation_id_propagation():
    """correlation_id on the task appears in audit log entries."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        server, mgr, transport, client = await _make_server(runtime)
        await client.send_task(
            "in-process://a2a",
            message="trace me",
            metadata={"correlation_id": "trace-corr-1"},
        )
        # Manually craft a task with explicit correlation_id (client
        # generates a task_id but doesn't surface correlation_id; we
        # set it directly here).
        explicit_task = A2ATask(
            requester="test-client",
            target="a2a-agent",
            message=A2AMessage.from_text("trace me too"),
            correlation_id="explicit-corr-42",
        )
        await server.receive(explicit_task)
        # Audit log should have an entry with the explicit correlation_id.
        entries = audit.filter(correlation_id="explicit-corr-42")
        assert len(entries) >= 1
        assert all(e.action == "a2a.task.receive" for e in entries)
    finally:
        await runtime.shutdown()


async def test_provenance_attached_to_a2a_request():
    """ProvenanceTracker records the inbound input on every receive."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        server, mgr, transport, client = await _make_server(runtime)
        task = await client.send_task("in-process://a2a", message="provenance probe")

        # The provenance tracker should have at least one input record
        # referencing our task_id (= execution_id).
        records = await prov.for_execution(task.task_id)
        inputs = [r for r in records if r.source == "test-client"]
        assert len(inputs) >= 1
        # Also output provenance from the target agent.
        outputs = [r for r in records if r.source == task.target]
        assert len(outputs) >= 1
    finally:
        await runtime.shutdown()


async def test_evidence_attached_to_a2a_response():
    """The response carries evidence_ids pointing at EvidenceManager records."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        server, mgr, transport, client = await _make_server(runtime)
        task = await client.send_task("in-process://a2a", message="evidence probe")

        assert task.state == TaskState.COMPLETED
        evidence_ids = task.metadata.get("evidence_ids", [])
        assert len(evidence_ids) >= 1
        # Each evidence_id should resolve in the EvidenceManager.
        for eid in evidence_ids:
            ev_record = await ev.retrieve(eid)
            assert ev_record is not None
            assert ev_record.execution_id == task.task_id
    finally:
        await runtime.shutdown()


async def test_audit_log_captures_a2a_operation():
    """AuditLog records action='a2a.task.receive' for every receive call."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        server, mgr, transport, client = await _make_server(runtime)
        await client.send_task("in-process://a2a", message="audit probe")
        await client.send_task("in-process://a2a", message="audit probe 2")

        entries = audit.filter(action="a2a.task.receive")
        assert len(entries) >= 2
        # Each entry's actor is the requester (the client).
        assert all(e.actor == "test-client" for e in entries)
        # Each entry's execution_id is the task_id (since we reuse it).
        assert all(e.execution_id for e in entries)
    finally:
        await runtime.shutdown()


# =====================================================================
# 5. A2ASecurityFilter — full enforcement
# =====================================================================


class _RejectingAuthenticator(Authenticator):
    """Authenticator that returns None for any token (denies all)."""

    name = "rejecting"
    is_mock = False

    async def authenticate(self, credentials: Any = None) -> AuthPrincipal:  # noqa: ARG002
        raise PermissionError("always rejected")

    async def verify(self, token: str | None = None) -> AuthPrincipal | None:  # noqa: ARG002
        return None


async def test_permission_denied_when_unauthorized():
    """With a rejecting authenticator, the task is denied with security_reason."""
    runtime, audit, prov, ev = await _make_runtime(with_security=True)
    try:
        runtime.register("authenticator", _RejectingAuthenticator())
        server, mgr, transport, client = await _make_server(runtime)
        task = await client.send_task("in-process://a2a", message="blocked")

        assert task.state == TaskState.FAILED
        assert "security_reason" in task.metadata
        assert "authentication_failed" in task.metadata["security_reason"]
        # No artifact produced for a denied task.
        assert not task.artifacts
    finally:
        await runtime.shutdown()


async def test_firewall_blocks_when_no_matching_rule():
    """ActionFirewall with DEFAULT-DENY blocks the unknown a2a action."""
    runtime, audit, prov, ev = await _make_runtime(with_security=True)
    try:
        server, mgr, transport, client = await _make_server(runtime)
        task = await client.send_task("in-process://a2a", message="blocked by fw")

        # ActionFirewall (no rules) → DENY for "a2a.task.receive".
        assert task.state == TaskState.FAILED
        assert "firewall_denied" in task.metadata["security_reason"]
    finally:
        await runtime.shutdown()


async def test_policy_engine_returns_require_hitl():
    """High-risk task → REQUIRE_HITL with approval_id, task NOT executed."""
    runtime, audit, prov, ev = await _make_runtime(with_security=True)
    try:
        # Replace firewall with a permissive one (rules that ALLOW a2a.task.receive
        # at LOW risk). The policy engine then exercises its own logic.
        allow_rule = FirewallRule(
            name="allow_a2a",
            action_pattern="a2a.task.receive",
            actor_pattern="*",
            risk_threshold=RiskLevel.LOW,
            autonomy_required=AutonomyLevel.L0_OBSERVE,
            decision=PolicyDecision.ALLOW,
            priority=10,
        )
        runtime.register(COMPONENT_FIREWALL, ActionFirewall(rules=[allow_rule]))
        # Policy rule: REQUIRE_HITL for HIGH-risk a2a tasks.
        hitl_rule = PolicyRule(
            name="hitl_high_risk_a2a",
            action_pattern="a2a.task.receive",
            actor_pattern="*",
            risk_threshold=RiskLevel.HIGH,
            autonomy_required=AutonomyLevel.L0_OBSERVE,
            decision=PolicyDecision.REQUIRE_HITL,
            priority=20,
        )
        engine = runtime.get_component(COMPONENT_POLICY_ENGINE)
        engine.add_rule(hitl_rule)

        server, mgr, transport, client = await _make_server(runtime)
        # Send a high-risk task via the security filter directly.
        task = A2ATask(
            requester="test-client",
            target="a2a-agent",
            message=A2AMessage.from_text("dangerous op"),
            metadata={"risk": "high"},
        )
        received = await server.receive(task)
        # REQUIRE_HITL: state stays SUBMITTED, approval_id is recorded.
        assert received.state == TaskState.SUBMITTED
        assert received.metadata.get("approval_id") is not None
        assert received.metadata.get("pending_approval_id") is not None
        # The approval engine has a PENDING request for this approval_id.
        approval_engine = runtime.get_component(COMPONENT_APPROVAL_ENGINE)
        approval_id = received.metadata["approval_id"]
        req = approval_engine.get(approval_id)
        assert req is not None
        assert str(req.status) == "ApprovalStatus.PENDING" or req.status.value == "pending"
    finally:
        await runtime.shutdown()


async def test_risk_classification_propagates():
    """The risk field in task.metadata propagates to the RunContext used by policy."""
    runtime, audit, prov, ev = await _make_runtime(with_security=True)
    try:
        # Permissive firewall so policy engine is the gate.
        allow_rule = FirewallRule(
            name="allow_all_a2a",
            action_pattern="a2a.task.*",
            actor_pattern="*",
            risk_threshold=RiskLevel.LOW,
            autonomy_required=AutonomyLevel.L0_OBSERVE,
            decision=PolicyDecision.ALLOW,
            priority=10,
        )
        runtime.register(COMPONENT_FIREWALL, ActionFirewall(rules=[allow_rule]))
        engine = runtime.get_component(COMPONENT_POLICY_ENGINE)
        # High-risk rule → REQUIRE_HITL; low-risk rule → ALLOW.
        hitl_rule = PolicyRule(
            name="hitl_high_risk",
            action_pattern="a2a.task.receive",
            actor_pattern="*",
            risk_threshold=RiskLevel.HIGH,
            decision=PolicyDecision.REQUIRE_HITL,
            priority=20,
        )
        allow_low = PolicyRule(
            name="allow_low_risk",
            action_pattern="a2a.task.receive",
            actor_pattern="*",
            risk_threshold=RiskLevel.LOW,
            decision=PolicyDecision.ALLOW,
            priority=10,
        )
        engine.add_rule(hitl_rule)
        engine.add_rule(allow_low)

        server, mgr, transport, client = await _make_server(runtime)

        # Low-risk task → ALLOWED → COMPLETED.
        low_task = A2ATask(
            requester="test-client",
            target="a2a-agent",
            message=A2AMessage.from_text("low risk op"),
            metadata={"risk": "low"},
        )
        low_out = await server.receive(low_task)
        assert low_out.state == TaskState.COMPLETED

        # High-risk task → REQUIRE_HITL → SUBMITTED.
        high_task = A2ATask(
            requester="test-client",
            target="a2a-agent",
            message=A2AMessage.from_text("high risk op"),
            metadata={"risk": "high"},
        )
        high_out = await server.receive(high_task)
        assert high_out.state == TaskState.SUBMITTED
        assert high_out.metadata.get("approval_id") is not None
    finally:
        await runtime.shutdown()


async def test_hitl_enforcement_pending_approval():
    """When REQUIRE_HITL is returned, the task stays SUBMITTED and the handler
    is NOT invoked."""
    runtime, audit, prov, ev = await _make_runtime(with_security=True)
    handler_calls = 0

    async def counting_handler(req: HandoffRequest) -> HandoffResponse:
        nonlocal handler_calls
        handler_calls += 1
        return HandoffResponse.success(req.execution_id, result={"ok": True})

    try:
        allow_rule = FirewallRule(
            name="allow_a2a",
            action_pattern="a2a.task.receive",
            actor_pattern="*",
            risk_threshold=RiskLevel.LOW,
            decision=PolicyDecision.ALLOW,
            priority=10,
        )
        runtime.register(COMPONENT_FIREWALL, ActionFirewall(rules=[allow_rule]))
        hitl_rule = PolicyRule(
            name="hitl_high",
            action_pattern="a2a.task.receive",
            actor_pattern="*",
            risk_threshold=RiskLevel.HIGH,
            decision=PolicyDecision.REQUIRE_HITL,
            priority=20,
        )
        runtime.get_component(COMPONENT_POLICY_ENGINE).add_rule(hitl_rule)

        server, mgr, transport, client = await _make_server(
            runtime, handler=counting_handler
        )
        task = A2ATask(
            requester="test-client",
            target="a2a-agent",
            message=A2AMessage.from_text("needs approval"),
            metadata={"risk": "high"},
        )
        out = await server.receive(task)
        # Task is NOT executed.
        assert out.state == TaskState.SUBMITTED
        assert handler_calls == 0
        assert out.metadata.get("approval_id") is not None
    finally:
        await runtime.shutdown()


async def test_unauthorized_a2a_request_rejected():
    """A bad token against a real authenticator → denied with security_reason."""
    runtime, audit, prov, ev = await _make_runtime(with_security=True)

    class _TokenAuthenticator(Authenticator):
        name = "token"
        is_mock = False

        async def authenticate(self, credentials: Any = None) -> AuthPrincipal:  # noqa: ARG002
            raise PermissionError("use verify()")

        async def verify(self, token: str | None = None) -> AuthPrincipal | None:
            if token == "good-token":
                return AuthPrincipal(
                    kind="agent",
                    identity="trusted-agent",
                    auth_method="token",
                    is_anonymous=False,
                    roles=["a2a-client"],
                )
            return None

    try:
        runtime.register("authenticator", _TokenAuthenticator())
        # Allow a2a.task.receive for low-risk requests via firewall.
        allow_rule = FirewallRule(
            name="allow_a2a",
            action_pattern="a2a.task.receive",
            actor_pattern="*",
            risk_threshold=RiskLevel.LOW,
            decision=PolicyDecision.ALLOW,
            priority=10,
        )
        runtime.register(COMPONENT_FIREWALL, ActionFirewall(rules=[allow_rule]))

        server, mgr, transport, client = await _make_server(runtime)
        # Bad token → denied.
        bad_client = A2AClient(InProcessTransport(), token="bad-token", requester="bad")
        bad_client.transport.register("in-process://a2a", server)
        task = await bad_client.send_task("in-process://a2a", message="malicious")
        assert task.state == TaskState.FAILED
        assert "authentication_failed" in task.metadata["security_reason"]
    finally:
        await runtime.shutdown()


# =====================================================================
# 6. CORE_ONLY mode + InProcessTransport
# =====================================================================


async def test_core_only_without_network():
    """Full A2A flow works in CORE_ONLY mode — no httpx, no network."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        server, mgr, transport, client = await _make_server(runtime)
        # Multiple sequential tasks complete successfully.
        for i in range(3):
            task = await client.send_task(
                "in-process://a2a", message=f"task-{i}"
            )
            assert task.state == TaskState.COMPLETED
        # All tasks are recorded in the server's internal store.
        assert len(server.list_tasks()) == 3
    finally:
        await runtime.shutdown()


async def test_in_process_transport_routes_to_server():
    """InProcessTransport routes by endpoint to the right server."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        ident_a = _identity(name="agent-a", endpoint="in-process://a")
        ident_b = _identity(name="agent-b", endpoint="in-process://b")
        server_a, _, transport, _ = await _make_server(runtime, identity=ident_a)
        server_b = A2AServer(
            runtime,
            identity=ident_b,
            handoff_manager=HandoffManager(),
        )
        transport.register("in-process://b", server_b)

        client = A2AClient(transport, requester="test-client")
        task_a = await client.send_task("in-process://a", message="to a")
        task_b = await client.send_task("in-process://b", message="to b")
        assert task_a.state == TaskState.COMPLETED
        assert task_b.state == TaskState.COMPLETED
        # Tasks landed in different servers.
        assert server_a.get_task(task_a.task_id) is not None
        assert server_b.get_task(task_b.task_id) is not None
        assert server_a.get_task(task_b.task_id) is None
    finally:
        await runtime.shutdown()


# =====================================================================
# 7. Malformed / timeout / idempotency / failure
# =====================================================================


async def test_malformed_a2a_request_rejected():
    """A task with no message and no objective → graceful FAILED state."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        server, mgr, transport, client = await _make_server(runtime)
        task = A2ATask(requester="x", target="a2a-agent", message=None)
        out = await server.receive(task)
        assert out.state == TaskState.FAILED
        assert "malformed" in out.metadata.get("error", "")
    finally:
        await runtime.shutdown()


async def test_a2a_timeout():
    """A deadline in the past → task fails immediately with timeout-like state."""
    runtime, audit, prov, ev = await _make_runtime()
    try:
        server, mgr, transport, client = await _make_server(runtime)
        from albra_seed.core.types import utc_now

        task = A2ATask(
            requester="x",
            target="a2a-agent",
            message=A2AMessage.from_text("too late"),
            deadline=utc_now() - timedelta(seconds=1),
        )
        out = await server.receive(task)
        assert out.state == TaskState.FAILED
        assert out.metadata.get("error") == "deadline_exceeded"
    finally:
        await runtime.shutdown()


async def test_a2a_idempotency():
    """Same task_id submitted twice → second returns stored result, handler
    NOT called again."""
    runtime, audit, prov, ev = await _make_runtime()
    handler_calls = 0

    async def counting_handler(req: HandoffRequest) -> HandoffResponse:
        nonlocal handler_calls
        handler_calls += 1
        return HandoffResponse.success(
            req.execution_id,
            result={"text": f"response-{handler_calls}"},
            confidence=0.5,
            responder=req.target,
        )

    try:
        server, mgr, transport, client = await _make_server(
            runtime, handler=counting_handler
        )
        task = A2ATask(
            task_id="idem-task-1",
            requester="test-client",
            target="a2a-agent",
            message=A2AMessage.from_text("do once"),
        )
        out1 = await server.receive(task)
        assert out1.state == TaskState.COMPLETED
        assert handler_calls == 1

        # Same task_id submitted again — should return stored result.
        out2 = await server.receive(task)
        assert out2.state == TaskState.COMPLETED
        # Handler was NOT called again.
        assert handler_calls == 1
        # Both responses are the same task object (idempotent).
        assert out1.task_id == out2.task_id
    finally:
        await runtime.shutdown()


async def test_a2a_failure_propagation():
    """When the handler raises, the task transitions to FAILED with the error
    surfaced in metadata."""
    runtime, audit, prov, ev = await _make_runtime()

    async def raising_handler(req: HandoffRequest) -> HandoffResponse:
        raise RuntimeError("boom from handler")

    try:
        server, mgr, transport, client = await _make_server(
            runtime, handler=raising_handler
        )
        task = await client.send_task("in-process://a2a", message="trigger failure")
        assert task.state == TaskState.FAILED
        # The error message is surfaced in metadata.errors.
        errors = task.metadata.get("errors", [])
        assert any("boom from handler" in e or "dispatch_error" in e for e in errors)
    finally:
        await runtime.shutdown()


# =====================================================================
# 8. HTTPTransport lazy import + A2ARegistry
# =====================================================================


def test_a2a_http_transport_lazy_import():
    """HTTPTransport raises NotImplementedError if httpx is not installed."""
    # Simulate httpx being unavailable by patching the import to raise.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "httpx":
            raise ImportError("simulated: httpx not installed")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, "__import__", side_effect=fake_import), pytest.raises(
        NotImplementedError
    ):
        # Either raises NotImplementedError immediately (if httpx is
        # actually installed, our fake_import forces ImportError) OR
        # if httpx isn't installed it raises naturally. Either way,
        # we expect NotImplementedError.
        HTTPTransport()


async def test_a2a_http_transport_methods_work_with_httpx():
    """When httpx IS installed, HTTPTransport methods do NOT raise
    NotImplementedError — they perform real HTTP via httpx.

    Uses ``httpx.MockTransport`` so no live network is required. The
    mock handler captures the JSON-RPC envelope and returns a canned
    A2A 1.0 wire response so we can verify the full round-trip.
    """
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        import json
        rpc = json.loads(body) if body else {}
        method = rpc.get("method", "")
        if method == "message/send":
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "id": rpc.get("id"),
                "result": {
                    "id": rpc.get("id", "task-x"),
                    "status": {"state": "completed", "message": None},
                    "message": None,
                    "artifacts": [],
                    "metadata": {},
                },
            })
        if method == "tasks/get":
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "id": rpc.get("id"),
                "result": {
                    "id": rpc.get("id", "task-x"),
                    "status": {"state": "working", "message": None},
                    "artifacts": [],
                },
            })
        if method == "tasks/cancel":
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "id": rpc.get("id"),
                "result": {
                    "id": rpc.get("id", "task-x"),
                    "status": {"state": "canceled", "message": None},
                    "artifacts": [],
                    "metadata": {},
                },
            })
        return httpx.Response(200, json={
            "jsonrpc": "2.0",
            "id": rpc.get("id"),
            "error": {"code": -32601, "message": "method not found"},
        })

    try:
        transport = HTTPTransport(transport=httpx.MockTransport(handler))
    except NotImplementedError:
        pytest.skip("httpx not installed; lazy-import test covers this path.")
        return

    # send → A2ATask (state=COMPLETED from the mock)
    task = A2ATask(
        requester="x", target="y", message=A2AMessage.from_text("hi"),
        task_id="task-send-1",
    )
    sent = await transport.send("https://example.com", task)
    assert sent.state == TaskState.COMPLETED

    # get_status → A2ATaskStatus (state=WORKING from the mock)
    status = await transport.get_status("https://example.com", "task-send-1")
    assert status is not None
    assert status.state == TaskState.WORKING

    # cancel → A2ATask (state=CANCELED from the mock)
    cancelled = await transport.cancel("https://example.com", "task-send-1")
    assert cancelled is not None
    assert cancelled.state == TaskState.CANCELED

    await transport.close()


def test_a2a_registry_register_and_lookup():
    """A2ARegistry supports register, get, list, is_available."""
    registry = A2ARegistry()
    record = A2AAgentRecord(
        agent_id="agent-X",
        name="Agent X",
        endpoint="in-process://x",
    )
    registry.register(record)

    # Lookup by id.
    got = registry.get("agent-X")
    assert got is not None
    assert got.name == "Agent X"
    assert got.endpoint == "in-process://x"

    # Lookup by endpoint.
    by_ep = registry.get_by_endpoint("in-process://x")
    assert by_ep is record

    # is_available defaults to True.
    assert registry.is_available("agent-X") is True

    # Set unavailable.
    assert registry.set_available("agent-X", False) is True
    assert registry.is_available("agent-X") is False

    # List returns all records.
    assert len(registry.list()) == 1

    # Unknown agent → None / False.
    assert registry.get("unknown") is None
    assert registry.is_available("unknown") is False
    assert registry.set_available("unknown", True) is False
