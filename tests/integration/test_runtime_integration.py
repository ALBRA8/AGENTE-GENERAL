"""ALBRA SEED — Integration tests.

These tests exercise multiple subsystems together — the Runtime, the
Agent loop, the firewall, the policy engine, the workflow engine, the
memory store, the audit log, the provider router with fallback, and the
approval engine. Each test wires a near-realistic Runtime in CORE_ONLY
mode (Mock provider + in-memory stores) and drives a flow that touches
at least 3 subsystems end-to-end.

Tests covered:
  1. test_runtime_full_lifecycle          — startup → register → run → shutdown
  2. test_agent_run_with_memory_and_audit   — agent run produces memory + audit
  3. test_firewall_blocks_high_risk_action — agent attempts high-risk action
  4. test_workflow_engine_with_hitl_resume — workflow pause + resume roundtrip
  5. test_provider_fallback_during_execution — router fallback mid-execution

CRITICAL: every test runs with NO external credentials.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from albra_seed.audit.normal import AuditLog
from albra_seed.core.base import LLMProvider
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import (
    AgentResponse,
    AutonomyLevel,
    EvidenceType,
    Message,
    PolicyDecision,
    RiskLevel,
    Role,
    RunContext,
    ToolCall,
    new_id,
)
from albra_seed.evidence.manager import EvidenceManager
from albra_seed.firewall import ActionFirewall, FirewallRule
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.providers.mock import MockProvider
from albra_seed.providers.registry import ProviderRegistry
from albra_seed.providers.router import ProviderRouter
from albra_seed.runtime import Agent, Runtime
from albra_seed.tools.infra_tools import EchoTool
from albra_seed.workflows.engine import WorkflowEngine
from albra_seed.workflows.executor import WorkflowExecutor
from albra_seed.workflows.graph import WorkflowGraph
from albra_seed.workflows.state import WorkflowRunStatus
from albra_seed.workflows.step import WorkflowStep, WorkflowStepType

# =====================================================================
# Helpers — adapters that satisfy the runtime's component contracts.
# =====================================================================


class _AuditAdapter:
    """Adapts AuditLog to the runtime's `await audit.append(run_ctx)` contract."""

    def __init__(self, log: AuditLog) -> None:
        self._log = log
        self.appended: list[RunContext] = []

    async def append(self, run_ctx: RunContext) -> None:
        self.appended.append(run_ctx)
        self._log.record(
            action="agent.run",
            actor=run_ctx.agent_id,
            execution_id=run_ctx.execution_id,
            correlation_id=run_ctx.correlation_id,
            result="failure" if run_ctx.errors else "success",
            error="; ".join(run_ctx.errors) if run_ctx.errors else None,
            metadata={
                "tools_used": list(run_ctx.tools_used),
                "providers_used": list(run_ctx.providers_used),
                "evidence_ids": list(run_ctx.evidence_ids),
            },
        )


class _EvidenceCollectorAdapter:
    """Adapts EvidenceManager to the runtime's `await ev.record(ctx)` contract."""

    def __init__(self, mgr: EvidenceManager) -> None:
        self._mgr = mgr
        self.records: list[Any] = []

    async def record(self, ctx: dict[str, Any]) -> Any:
        run_ctx: RunContext = ctx.get("context")
        if run_ctx is None:
            return None
        ev = await self._mgr.register(
            source="agent",
            source_type="agent",
            content=run_ctx.task,
            evidence_type=EvidenceType.INFERENCE,
            confidence=0.5,
            execution_id=run_ctx.execution_id,
        )
        self.records.append(ev)
        return ev


class _RouterCallingMock(LLMProvider):
    """Mock provider that returns an AgentResponse with an echo tool_call."""

    name = "router_mock"
    is_mock = True
    is_external = False

    def __init__(self, *, echo_message: str = "router-echo") -> None:
        self._echo_message = echo_message
        self.calls = 0

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        context: RunContext | None = None,
    ) -> AgentResponse:
        self.calls += 1
        return AgentResponse(
            content="[mock] routed",
            execution_id=context.execution_id if context else new_id("exec"),
            correlation_id=context.correlation_id if context else new_id("corr"),
            confidence=0.7,
            tool_calls=[
                ToolCall(
                    id=new_id("tc"),
                    tool_name="echo",
                    arguments={"message": self._echo_message},
                )
            ],
        )

    async def health(self) -> bool:
        return True


class _FailingProvider(LLMProvider):
    """Provider that always raises a transient-looking exception."""

    name = "failing"
    is_mock = False
    is_external = True

    async def complete(self, messages: list[Message], **kwargs: Any) -> AgentResponse:
        raise ConnectionError("upstream timeout")

    async def health(self) -> bool:
        return False


def _identity(autonomy: AutonomyLevel = AutonomyLevel.L2_EXECUTE_SAFE) -> AgentIdentity:
    return AgentIdentity(
        name="integration-agent",
        autonomy_level=autonomy,
        providers=["mock"],
        permissions=["tool.echo"],
    )


# =====================================================================
# 1. Runtime full lifecycle
# =====================================================================


async def test_runtime_full_lifecycle() -> None:
    """Startup → register components → run agent → shutdown."""
    runtime = Runtime()
    assert runtime.lifecycle.phase.value == "uninitialized"

    await runtime.startup()
    assert runtime.lifecycle.phase.value == "ready"

    # Register components — minimal but realistic.
    runtime.register_provider(MockProvider())
    runtime.register_tool(EchoTool())
    memory = InMemoryMemoryStore()
    runtime.register_memory_store("default", memory)
    # Also register the memory store as a named component so health()
    # accurately reports it as configured.
    from albra_seed.runtime import COMPONENT_MEMORY_STORE
    runtime.register(COMPONENT_MEMORY_STORE, memory)
    audit = AuditLog()
    runtime.register("audit_logger", _AuditAdapter(audit))
    runtime.register("evidence_collector", _EvidenceCollectorAdapter(EvidenceManager()))

    # Readiness requires provider_router — register it.
    router = ProviderRouter()
    runtime.register("provider_router", router)
    assert runtime.readiness() is True

    # Run an agent end-to-end.
    agent = Agent(_identity(), runtime)
    response = await agent.run("integration check")
    assert response.content == "[mock] integration check"
    assert response.execution_id

    # Health reflects the configured state.
    health = runtime.health()
    assert health["status"] in {"healthy", "degraded"}
    assert health["components"]["audit_logger"] == "configured"
    assert health["components"]["memory_store"] == "configured"
    assert health["sessions"] == 1  # session was registered during run

    await runtime.shutdown()
    assert runtime.lifecycle.phase.value == "stopped"
    # Sessions dropped on shutdown.
    assert runtime.active_session_count == 0


# =====================================================================
# 2. Agent run produces memory record + audit entry
# =====================================================================


async def test_agent_run_with_memory_and_audit() -> None:
    """An agent run must persist a memory record AND an audit entry."""
    runtime = Runtime()
    await runtime.startup()

    memory = InMemoryMemoryStore()
    runtime.register_memory_store("default", memory)
    runtime.register_provider(MockProvider())
    runtime.register_tool(EchoTool())
    audit_log = AuditLog()
    audit_adapter = _AuditAdapter(audit_log)
    runtime.register("audit_logger", audit_adapter)
    evidence_mgr = EvidenceManager()
    runtime.register("evidence_collector", _EvidenceCollectorAdapter(evidence_mgr))

    agent = Agent(_identity(), runtime)
    response = await agent.run("remember this")

    # Memory: a working record was written by the RECORD phase.
    records = await memory.query(agent_id=agent.identity.agent_id)
    assert len(records) >= 1
    assert records[0].content == "remember this"
    assert records[0].execution_id == response.execution_id

    # Audit: one entry per run (at least the append from the RECORD phase).
    assert len(audit_adapter.appended) == 1
    appended_ctx = audit_adapter.appended[0]
    assert appended_ctx.execution_id == response.execution_id
    audit_entries = audit_log.filter_by_execution_id(response.execution_id)
    assert len(audit_entries) >= 1
    assert audit_entries[0].action == "agent.run"

    # Evidence collector recorded at least one evidence entry.
    assert len(evidence_mgr.store._records) >= 1  # noqa: SLF001
    # And the RunContext carried the evidence_id forward.
    ctx = agent.state.current_context
    assert ctx.evidence_ids

    await runtime.shutdown()


# =====================================================================
# 3. Firewall blocks a high-risk action
# =====================================================================


async def test_firewall_blocks_high_risk_action() -> None:
    """Agent attempts a high-risk action; the firewall denies it."""
    runtime = Runtime()
    await runtime.startup()

    # Provider returns a tool_call for "risky_op".
    class _RiskyProvider(LLMProvider):
        name = "risky_mock"
        is_mock = True
        is_external = False

        async def complete(
            self,
            messages: list[Message],
            *,
            model: str | None = None,
            temperature: float = 0.2,
            max_tokens: int = 2048,
            tools: list[dict] | None = None,
            context: RunContext | None = None,
        ) -> AgentResponse:
            return AgentResponse(
                content="[mock] risky",
                execution_id=context.execution_id if context else new_id("exec"),
                correlation_id=context.correlation_id if context else new_id("corr"),
                tool_calls=[
                    ToolCall(
                        id=new_id("tc"),
                        tool_name="risky_op",
                        arguments={"x": 1},
                    )
                ],
            )

        async def health(self) -> bool:
            return True

    runtime.register_provider(_RiskyProvider())

    # Register the risky tool.
    class _RiskyTool(EchoTool):
        def __init__(self) -> None:
            from albra_seed.core.base import ToolContract

            self.contract = ToolContract(
                name="risky_op",
                description="a risky operation",
                risk_level=RiskLevel.HIGH,
            )

    runtime.register_tool(_RiskyTool())

    # Firewall denies risky_op explicitly.
    firewall = ActionFirewall(
        rules=[
            FirewallRule(
                name="deny_risky",
                action_pattern="risky_op",
                risk_threshold=RiskLevel.UNKNOWN,
                decision=PolicyDecision.DENY,
                priority=100,
            )
        ]
    )
    runtime.register("firewall", firewall)

    agent = Agent(_identity(autonomy=AutonomyLevel.L2_EXECUTE_SAFE), runtime)
    response = await agent.run("do risky thing")

    # The tool result is a failure with "blocked" reason.
    assert response.tool_results
    blocked = response.tool_results[0]
    assert blocked.success is False
    assert "blocked" in (blocked.error or "")
    # Context recorded the denial as a warning.
    ctx = agent.state.current_context
    assert any("denied" in w for w in ctx.warnings)
    await runtime.shutdown()


# =====================================================================
# 4. Workflow engine with HITL pause/resume (integration)
# =====================================================================


async def test_workflow_engine_with_hitl_resume() -> None:
    """A full workflow with HITL pause + resume via WorkflowEngine."""
    graph = WorkflowGraph(name="approval-flow")
    graph.add_step(WorkflowStep(id="draft", name="draft"))
    graph.add_step(WorkflowStep(id="approve", name="approve", type=WorkflowStepType.HITL))
    graph.add_step(WorkflowStep(id="publish", name="publish"))
    graph.add_edges(("draft", "approve"), ("approve", "publish"))

    artifacts: list[str] = []

    async def draft(step: WorkflowStep, state: Any, ctx: RunContext) -> dict[str, Any]:
        artifacts.append("drafted")
        return {"draft": "ready"}

    async def publish(step: WorkflowStep, state: Any, ctx: RunContext) -> dict[str, Any]:
        artifacts.append("published")
        return {"published": True}

    engine = WorkflowEngine(
        executor=WorkflowExecutor(handlers={"draft": draft, "publish": publish})
    )
    engine.register_graph(graph)

    # First run: pauses at HITL.
    state, trace = await engine.run("approval-flow")
    assert state.status == WorkflowRunStatus.PAUSED
    assert state.hitl_pending == "approve"
    assert "draft" in state.visited
    assert "publish" not in state.visited
    assert artifacts == ["drafted"]

    # Resume after human approval.
    state, trace = await engine.resume(
        "approval-flow", state, human_input={"approved": True}
    )
    assert state.status == WorkflowRunStatus.SUCCEEDED
    assert "approve" in state.visited
    assert "publish" in state.visited
    assert artifacts == ["drafted", "published"]



# =====================================================================
# 6. Agent HITL suspend/approve/resume lifecycle
# =====================================================================


@pytest.mark.parametrize("human_decision", ["approve", "reject", "expire", "identity_changed"])
async def test_agent_suspends_and_resumes_same_bound_tool_call_once(
    human_decision: str,
) -> None:
    """A pending approval suspends the run; only a still-valid principal approval dispatches."""
    from albra_seed.approvals import ApprovalEngine
    from albra_seed.core.types import ApprovalStatus
    from albra_seed.policy import PolicyEngine, PolicyRule, PolicyRuleSet
    from albra_seed.security.auth import AuthPrincipal, Authenticator

    class _IntegrationAuthenticator(Authenticator):
        name = "hitl-integration"

        def __init__(self) -> None:
            self.identity = "requester-1"

        async def authenticate(self, credentials: Any) -> AuthPrincipal:
            return AuthPrincipal(
                kind="user", identity=self.identity, roles=["user"],
                auth_method=self.name, is_anonymous=False,
            )

        async def verify(self, token: str | None) -> AuthPrincipal | None:
            if token != "requester-token":
                return None
            return AuthPrincipal(
                kind="user", identity=self.identity, roles=["user"],
                auth_method=self.name, is_anonymous=False,
            )

    class _CountingEcho(EchoTool):
        def __init__(self) -> None:
            super().__init__()
            self.execute_count = 0

        async def execute(self, *args: Any, **kwargs: Any):
            self.execute_count += 1
            return await super().execute(*args, **kwargs)

    runtime = Runtime()
    await runtime.startup()
    provider = _RouterCallingMock(echo_message="approved-once")
    runtime.register_provider(provider)
    tool = _CountingEcho()
    runtime.register_tool(tool)

    firewall = ActionFirewall(rules=[
        FirewallRule(
            name="allow_echo",
            action_pattern="echo",
            actor_pattern="*",
            risk_threshold=RiskLevel.UNKNOWN,
            decision=PolicyDecision.ALLOW,
            autonomy_required=0,
        )
    ])
    runtime.register("firewall", firewall)

    policy = PolicyEngine(rule_set=PolicyRuleSet())
    policy.add_rule(PolicyRule(
        name="echo_requires_human",
        action_pattern="echo",
        actor_pattern="*",
        risk_threshold=RiskLevel.UNKNOWN,
        autonomy_required=AutonomyLevel.L3_EXECUTE_EXTERNAL,
        decision=PolicyDecision.ALLOW,
        priority=100,
    ))
    runtime.register("policy_engine", policy)
    approvals = ApprovalEngine(default_deadline_seconds=5)
    runtime.register("approval_engine", approvals)
    authenticator = _IntegrationAuthenticator()
    runtime.register("authenticator", authenticator)

    identity = AgentIdentity(
        name="hitl-integration-agent",
        autonomy_level=AutonomyLevel.L0_OBSERVE,
        providers=["router_mock"],
        permissions=["tool.echo"],
    )
    agent = Agent(identity, runtime)
    pending_run = asyncio.create_task(agent.run("perform echo", token="requester-token"))

    try:
        pending = None
        for _ in range(200):
            items = approvals.list_pending()
            if items:
                pending = items[0]
                break
            await asyncio.sleep(0.005)
        assert pending is not None, "Agent.run did not suspend on a pending approval"
        assert not pending_run.done(), "Agent.run returned instead of suspending"
        assert tool.execute_count == 0, "tool ran before human approval"

        if human_decision in {"approve", "identity_changed"}:
            if human_decision == "identity_changed":
                # Simulate revocation/account substitution while the run waits.
                authenticator.identity = "different-user"
            decision = await approvals.approve(pending.approval_id, approver="reviewer-1")
            assert decision.status == ApprovalStatus.APPROVED
        elif human_decision == "reject":
            decision = await approvals.reject(
                pending.approval_id, approver="reviewer-1", reason="test rejection"
            )
            assert decision.status == ApprovalStatus.REJECTED
        else:
            decision = await approvals.expire(pending.approval_id)
            assert decision.status == ApprovalStatus.EXPIRED
        response = await asyncio.wait_for(pending_run, timeout=5)

        assert response.execution_id == pending.context_ref["execution_id"]
        if human_decision == "approve":
            assert tool.execute_count == 1, "approved tool call must dispatch exactly once"
            assert response.tool_results and response.tool_results[0].success
            assert response.tool_results[0].output["echoed"] == "approved-once"
            assert (
                pending.context_ref["execution_id"],
                pending.context_ref["tool_call_id"],
            ) in approvals._claimed_tool_calls, "the approved call must remain claimed after dispatch"
        else:
            assert tool.execute_count == 0, "rejected/expired call must never dispatch"
    finally:
        if not pending_run.done():
            pending_run.cancel()
            try:
                await pending_run
            except asyncio.CancelledError:
                pass
        await runtime.shutdown()


# =====================================================================
# 5. Provider fallback during execution
# =====================================================================


async def test_provider_fallback_during_execution() -> None:
    """When the primary provider fails mid-execution, fallback kicks in."""
    registry = ProviderRegistry()
    registry.register(_FailingProvider())
    # Mock is auto-registered.

    router = ProviderRouter(
        registry=registry,
        default_provider="failing",  # primary
        fallback_chain=["failing", "mock"],
    )

    # Build a RunContext like the agent would.
    run_ctx = RunContext(agent_id="agent-X", task="demo")
    response = await router.complete(
        [Message(role=Role.USER, content="help me")],
        context=run_ctx,
    )

    # Mock fallback produced the response.
    assert response.content.startswith("[mock]")
    # Router tagged metadata with router info.
    assert response.metadata.get("router") == "provider_router"
    assert response.metadata.get("router_latency_ms") is not None
    # The context records the providers consulted.
    assert "failing" in run_ctx.providers_used or "mock" in run_ctx.providers_used

    # Router's underlying health snapshot reflects the failure.
    snap = registry.snapshot()
    failing_health = snap["health"]["failing"]
    # The failing provider's metrics recorded at least one error.
    assert failing_health["metrics"]["errors"] >= 1
    assert failing_health["metrics"]["last_error"] is not None
    assert "timeout" in (failing_health["metrics"]["last_error"] or "").lower()
    # Mock still reports healthy (fallback succeeded).
    mock_health = snap["health"]["mock"]
    assert mock_health["metrics"]["successes"] >= 1
