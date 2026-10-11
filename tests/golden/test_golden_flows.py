"""ALBRA SEED — Golden Flows.

This module implements and verifies the 13 canonical golden flows
defined in the ALBRA SEED specification (section 57). Each test wires up
a minimal Runtime (Mock provider + in-memory stores) and exercises an
end-to-end flow that the SEED MUST support in CORE_ONLY mode — no
external credentials, no domain scenarios, fully deterministic.

Golden flows covered:
   1. receive task → reason → respond
   2. execute safe tool (EchoTool)
   3. high-risk tool triggers HITL approval
   4. denied action blocked by ActionFirewall
   5. provider fails → fallback chain activates
   6. workflow step fails then succeeds on retry
   7. workflow pauses for HITL, resumes after approval
   8. memory write includes provenance + evidence
   9. execution produces full trace with evidence IDs
  10. agent A hands off to agent B
  11. Doctor reports DEGRADED when a component fails
  12. RecoveryManager retries on transient error
  13. kill switch activation blocks ALL external actions

CRITICAL: every test runs with NO external credentials. The Mock
provider is always available so CORE_ONLY mode works end-to-end.
"""

from __future__ import annotations

from typing import Any

import pytest

from albra_seed.approvals.engine import ApprovalResult
from albra_seed.core.base import LLMProvider, MemoryRecord
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import (
    AgentResponse,
    ApprovalStatus,
    AutonomyLevel,
    HealthState,
    MemoryType,
    Message,
    PolicyDecision,
    RiskLevel,
    Role,
    RunContext,
    ToolCall,
    new_id,
)
from albra_seed.doctor.checks import CheckRegistry, CheckResult
from albra_seed.doctor.doctor import Doctor
from albra_seed.doctor.report import ImplementationStatus
from albra_seed.execution.trace import ExecutionTrace
from albra_seed.firewall import ActionFirewall, FirewallRule, KillSwitch
from albra_seed.handoff.contract import (
    HandoffRequest,
    HandoffResponse,
    HandoffStatus,
)
from albra_seed.handoff.manager import HandoffManager
from albra_seed.handoff.registry import AgentRecord, AgentRegistry
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.memory.provenance import MemoryProvenance
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule
from albra_seed.providers.fallback import ProviderFallback
from albra_seed.providers.mock import MockProvider
from albra_seed.providers.registry import ProviderRegistry
from albra_seed.recovery.manager import RecoveryManager
from albra_seed.recovery.strategies import RetryStrategy
from albra_seed.runtime import Agent, Runtime
from albra_seed.tools.infra_tools import EchoTool
from albra_seed.workflows.executor import WorkflowExecutor
from albra_seed.workflows.graph import WorkflowGraph
from albra_seed.workflows.state import WorkflowRunStatus
from albra_seed.workflows.step import RetryPolicy, WorkflowStep, WorkflowStepType

# =====================================================================
# SHARED HELPERS — minimal adapters usable by every golden flow
# =====================================================================


class _AutoApprovalEngine:
    """Stub approval engine that auto-approves every request immediately.

    The real ApprovalEngine creates PENDING requests; this stub demonstrates
    the "approval granted" branch of the HITL golden flow without
    introducing real-world approver latency.
    """

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def request(
        self,
        action: Any,
        context: RunContext | None = None,
        deadline: Any = None,
        **kwargs: Any,
    ) -> ApprovalResult:
        self.requests.append((action, context))
        return ApprovalResult(
            approval_id=new_id("appr"),
            status=ApprovalStatus.APPROVED,
            decided_by="auto-approver",
        )


class _ProviderCallingTool(LLMProvider):
    """Mock provider that emits a fixed tool_call every completion."""

    name = "tool_calling_mock"
    is_mock = True
    is_external = False

    def __init__(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._arguments = arguments
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
            content="[mock] dispatched tool",
            execution_id=context.execution_id if context else new_id("exec"),
            correlation_id=context.correlation_id if context else new_id("corr"),
            confidence=0.8,
            tool_calls=[
                ToolCall(
                    id=new_id("tc"),
                    tool_name=self._tool_name,
                    arguments=dict(self._arguments),
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
        raise ConnectionError("simulated network timeout")

    async def health(self) -> bool:
        return False


def _identity(name: str = "test-agent", autonomy: AutonomyLevel = AutonomyLevel.L2_EXECUTE_SAFE) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        autonomy_level=autonomy,
        providers=["mock"],
        permissions=["tool.echo"],
    )


# =====================================================================
# 1. GOLDEN: receive task → reason → respond
# =====================================================================


async def test_golden_receive_task_reason_respond() -> None:
    """Receive a task → Mock provider reasons → agent returns response."""
    runtime = Runtime()
    await runtime.startup()
    runtime.register_provider(MockProvider())
    agent = Agent(_identity(), runtime)

    response = await agent.run("hello world")

    assert isinstance(response, AgentResponse)
    assert response.role == Role.ASSISTANT
    assert response.content == "[mock] hello world"
    assert response.execution_id  # non-empty
    assert response.correlation_id  # non-empty
    # All 10 canonical phases ran.
    ctx = agent.state.current_context
    assert ctx is not None
    trace = ctx.outputs.get("trace", [])
    assert [e["phase"] for e in trace][0] == "RECEIVE"
    assert [e["phase"] for e in trace][-1] == "RESPOND"
    assert len(trace) == 10
    await runtime.shutdown()


# =====================================================================
# 2. GOLDEN: execute safe tool (EchoTool)
# =====================================================================


async def test_golden_execute_safe_tool() -> None:
    """Agent invokes EchoTool successfully end-to-end."""
    runtime = Runtime()
    await runtime.startup()
    runtime.register_provider(_ProviderCallingTool("echo", {"message": "hi"}))
    runtime.register_tool(EchoTool())
    agent = Agent(_identity(), runtime)

    response = await agent.run("echo hi")

    assert response.tool_calls, "agent must emit a tool_call"
    assert response.tool_results, "agent must execute the tool"
    result = response.tool_results[0]
    assert result.success is True
    assert result.output["echoed"] == "hi"
    assert "echo" in agent.state.current_context.tools_used
    await runtime.shutdown()


# =====================================================================
# 3. GOLDEN: high-risk tool triggers HITL approval
# =====================================================================


async def test_golden_tool_requires_approval() -> None:
    """High-risk tool triggers HITL approval; approval granted → tool runs."""
    runtime = Runtime()
    await runtime.startup()
    runtime.register_provider(_ProviderCallingTool("dangerous_op", {"x": 1}))
    runtime.register_tool(EchoTool())  # registered but with contract name=echo

    # Re-register a "dangerous_op" tool with high-risk contract so it executes.
    class _DangerousTool(EchoTool):
        def __init__(self) -> None:
            from albra_seed.core.base import ToolContract

            self.contract = ToolContract(
                name="dangerous_op",
                description="high-risk operation requiring HITL",
                risk_level=RiskLevel.HIGH,
                is_external=False,
            )

    runtime.register_tool(_DangerousTool())
    policy = PolicyEngine(default_decision=PolicyDecision.DENY)
    policy.add_rule(
        PolicyRule(
            name="require_hitl_for_dangerous",
            action_pattern="dangerous_*",
            risk_threshold=RiskLevel.UNKNOWN,
            decision=PolicyDecision.REQUIRE_HITL,
            priority=10,
        )
    )
    runtime.register("policy_engine", policy)
    approval = _AutoApprovalEngine()
    runtime.register("approval_engine", approval)

    agent = Agent(_identity(autonomy=AutonomyLevel.L1_RECOMMEND), runtime)
    response = await agent.run("run dangerous op")

    # The auto-approver was consulted.
    assert len(approval.requests) == 1
    # The approval_id was recorded on the RunContext.
    ctx = agent.state.current_context
    assert ctx.approvals, "approval_id should be recorded on context"
    # The tool ran successfully.
    assert response.tool_results, "tool should have executed"
    assert response.tool_results[0].success is True
    await runtime.shutdown()


# =====================================================================
# 4. GOLDEN: action blocked by ActionFirewall
# =====================================================================


async def test_golden_action_blocked_by_firewall() -> None:
    """Denied action is blocked by ActionFirewall — tool never executes."""
    runtime = Runtime()
    await runtime.startup()
    runtime.register_provider(_ProviderCallingTool("blocked_op", {"x": 1}))
    runtime.register_tool(EchoTool())

    class _BlockedTool(EchoTool):
        def __init__(self) -> None:
            from albra_seed.core.base import ToolContract

            self.contract = ToolContract(
                name="blocked_op",
                description="operation the firewall denies",
                risk_level=RiskLevel.HIGH,
            )

    runtime.register_tool(_BlockedTool())
    firewall = ActionFirewall(
        rules=[
            FirewallRule(
                name="deny_blocked_op",
                action_pattern="blocked_op",
                decision=PolicyDecision.DENY,
                priority=100,
            )
        ]
    )
    runtime.register("firewall", firewall)

    agent = Agent(_identity(autonomy=AutonomyLevel.L2_EXECUTE_SAFE), runtime)
    response = await agent.run("do blocked op")

    # Tool result recorded as failure with a "blocked" reason.
    assert response.tool_results, "blocked result is still recorded"
    blocked = response.tool_results[0]
    assert blocked.success is False
    assert "blocked" in (blocked.error or "")
    # Context recorded a warning about the denial.
    ctx = agent.state.current_context
    assert any("denied" in w for w in ctx.warnings)
    await runtime.shutdown()


# =====================================================================
# 5. GOLDEN: provider fails → fallback chain activates
# =====================================================================


async def test_golden_provider_fails_then_fallback() -> None:
    """A failing provider triggers fallback to Mock — no exception escapes."""
    registry = ProviderRegistry()
    registry.register(_FailingProvider())
    # Mock is auto-registered by ProviderRegistry.
    fb = ProviderFallback(["failing"], registry)

    response = await fb.complete([Message(role=Role.USER, content="hi")])

    # Fallback chain moved past `failing` to Mock.
    assert response.content.startswith("[mock]")
    assert len(fb.transitions) >= 1
    transition = fb.transitions[0]
    assert transition["provider"] == "failing"
    assert transition["kind"] == "exception"
    assert "timeout" in transition["error"].lower()


# =====================================================================
# 6. GOLDEN: workflow step fails then succeeds on retry
# =====================================================================


async def test_golden_workflow_with_retry() -> None:
    """A workflow step that fails twice then succeeds — retries kick in."""
    counter = {"n": 0}

    async def flaky_handler(step: WorkflowStep, state: Any, context: RunContext) -> dict[str, Any]:
        counter["n"] += 1
        if counter["n"] < 3:
            raise RuntimeError("transient failure")
        return {"status": "ok", "attempts": counter["n"]}

    graph = WorkflowGraph(name="retry-flow")
    graph.add_step(
        WorkflowStep(
            id="flaky",
            name="flaky",
            retry_policy=RetryPolicy(
                max_retries=3, base_delay_seconds=0.001, max_delay_seconds=0.01
            ),
        )
    )
    executor = WorkflowExecutor(handlers={"flaky": flaky_handler})
    state = await executor.execute(graph)

    assert state.status == WorkflowRunStatus.SUCCEEDED
    assert counter["n"] == 3
    assert state.step_outputs["flaky"]["status"] == "ok"


# =====================================================================
# 7. GOLDEN: workflow pauses for HITL, resumes after approval
# =====================================================================


async def test_golden_workflow_with_hitl() -> None:
    """Workflow pauses at HITL step; resume() completes it after approval."""
    graph = WorkflowGraph(name="hitl-flow")
    graph.add_step(WorkflowStep(id="prepare", name="prepare"))
    graph.add_step(
        WorkflowStep(id="approve", name="approve", type=WorkflowStepType.HITL)
    )
    graph.add_step(WorkflowStep(id="finalize", name="finalize"))
    graph.add_edges(("prepare", "approve"), ("approve", "finalize"))

    async def prepare(step: WorkflowStep, state: Any, ctx: RunContext) -> dict[str, Any]:
        return {"draft": "ready"}

    async def finalize(step: WorkflowStep, state: Any, ctx: RunContext) -> dict[str, Any]:
        return {"final": True}

    executor = WorkflowExecutor(handlers={"prepare": prepare, "finalize": finalize})

    state = await executor.execute(graph)
    # Execution paused at the HITL step.
    assert state.status == WorkflowRunStatus.PAUSED
    assert state.hitl_pending == "approve"
    assert state.visited == ["prepare"]

    # Human approves → resume.
    state = await executor.resume(graph, state, human_input={"approved": True})
    assert state.status == WorkflowRunStatus.SUCCEEDED
    assert "approve" in state.visited
    assert "finalize" in state.visited


# =====================================================================
# 8. GOLDEN: memory write includes provenance + evidence
# =====================================================================


async def test_golden_memory_write_with_provenance() -> None:
    """A memory write must carry provenance and evidence identifiers."""
    store = InMemoryMemoryStore()
    provenance_builder = MemoryProvenance(default_source="agent", default_source_type="agent")
    record = MemoryRecord(
        agent_id="agent-A",
        domain="default",
        memory_type=MemoryType.FACTUAL,
        content="the system is healthy",
        confidence=0.95,
        evidence_ids=["ev_001", "ev_002"],
        execution_id="exec-1",
        correlation_id="corr-1",
        source="tool",
        source_type="tool",
        is_fact=True,
    )
    record, prov = provenance_builder.attach(record)
    record_id = await store.write(record)

    assert record_id == record.record_id
    assert record.provenance_id == prov.provenance_id
    assert prov.evidence_ids == ["ev_001", "ev_002"]
    assert prov.execution_id == "exec-1"
    # Factual memory must satisfy the UNIVERSAL FACT rule (single source of
    # truth in MemoryProvenance.validate_fact): evidence, source_type, and
    # confidence are all required.
    is_valid, why = MemoryProvenance.validate_fact(record)
    assert is_valid is True, why
    # Read back.
    fetched = await store.read(record_id)
    assert fetched is not None
    assert fetched.content == "the system is healthy"


# =====================================================================
# 9. GOLDEN: execution produces full trace with evidence IDs
# =====================================================================


async def test_golden_evidence_execution_trace() -> None:
    """A multi-phase execution produces a trace with evidence IDs."""
    trace = ExecutionTrace(
        execution_id="exec-trace-1",
        correlation_id="corr-trace-1",
        agent_id="agent-A",
        task="demo task",
    )
    s1 = trace.begin_step("RECEIVE", inputs={"message": "hello"})
    s1.add_decision("received", source="user")
    s1.finish(outputs={"received": True}, status="success")

    s2 = trace.begin_step("EXECUTE", inputs={"tool": "echo"})
    s2.add_tool_call("echo", call_id="tc_1")
    s2.add_evidence("ev_001")
    s2.add_evidence("ev_002")
    s2.finish(outputs={"echoed": "hello"}, status="success")

    s3 = trace.begin_step("RESPOND", inputs={})
    s3.add_evidence("ev_003")
    s3.finish(outputs={"content": "[mock] hello"}, status="success")

    trace.finish(status="succeeded")

    assert len(trace.steps) == 3
    assert trace.steps[0].phase == "RECEIVE"
    assert trace.steps[1].tool_calls[0]["tool"] == "echo"
    # Evidence rolled up to the trace level.
    assert trace.evidence_ids == ["ev_001", "ev_002", "ev_003"]
    assert trace.is_success is True


# =====================================================================
# 10. GOLDEN: agent A hands off to agent B
# =====================================================================


async def test_golden_handoff_between_agents() -> None:
    """Agent A hands off a task to agent B; B responds with a success."""
    registry = AgentRegistry()

    async def agent_b_handler(request: HandoffRequest) -> HandoffResponse:
        return HandoffResponse.success(
            request.execution_id,
            result={"summary": "handled by B"},
            confidence=0.9,
            responder="agent-B",
        )

    registry.register(
        AgentRecord(agent_id="agent-B", name="Agent B", handler=agent_b_handler)
    )
    manager = HandoffManager(registry=registry)

    request = HandoffRequest(
        execution_id=new_id("exec"),
        requester="agent-A",
        target="agent-B",
        objective="compute result",
        input={"payload": "handoff"},
    )
    response = await manager.initiate(request)

    assert response.status == HandoffStatus.SUCCEEDED
    assert response.responder == "agent-B"
    assert response.result == {"summary": "handled by B"}
    assert response.confidence == pytest.approx(0.9)
    # Evidence chain recorded the handoff.
    assert len(manager.evidence_chain) >= 1
    assert manager.evidence_chain.verify_chain() is True


# =====================================================================
# 11. GOLDEN: Doctor reports DEGRADED when a component fails
# =====================================================================


async def test_golden_doctor_detects_degraded_component() -> None:
    """When one check returns DEGRADED, the report's overall_status reflects it."""
    registry = CheckRegistry()

    async def healthy_check() -> CheckResult:
        return CheckResult(
            name="runtime",
            status=HealthState.HEALTHY,
            implementation_status=ImplementationStatus.IMPLEMENTED,
            details="ok",
        )

    async def degraded_check() -> CheckResult:
        return CheckResult(
            name="providers",
            status=HealthState.DEGRADED,
            implementation_status=ImplementationStatus.IMPLEMENTED,
            details="primary provider unreachable; fallback active",
        )

    registry.register("runtime", healthy_check)
    registry.register("providers", degraded_check)
    doctor = Doctor(registry=registry)

    report = await doctor.diagnose()

    by_name = {c.name: c for c in report.components}
    assert by_name["runtime"].status == HealthState.HEALTHY
    assert by_name["providers"].status == HealthState.DEGRADED
    # Overall status is the WORST observed.
    assert report.overall_status == HealthState.DEGRADED


# =====================================================================
# 12. GOLDEN: RecoveryManager retries on transient error
# =====================================================================


async def test_golden_safe_recovery() -> None:
    """A transient error is classified + retried; the call eventually succeeds."""
    state = {"attempts": 0}

    async def flaky_call() -> str:
        state["attempts"] += 1
        if state["attempts"] < 2:
            raise ConnectionError("transient connection timeout")
        return "recovered"

    manager = RecoveryManager(
        default_retry=RetryStrategy(
            max_attempts=3, base_delay_seconds=0.001, max_delay_seconds=0.01
        )
    )

    result = await manager.handle("test-component", flaky_call)

    assert result == "recovered"
    assert state["attempts"] == 2
    # Audit trail records the recovery action.
    audit = manager.audit()
    counts = audit.count()
    assert counts["tamper_evident"] >= 1
    assert manager.verify_audit_integrity() is True


# =====================================================================
# 13. GOLDEN: kill switch activation blocks all external actions
# =====================================================================


async def test_golden_kill_switch() -> None:
    """Activating the KillSwitch blocks every action through the firewall."""
    kill = KillSwitch()
    firewall = ActionFirewall(
        rules=[
            FirewallRule(
                name="allow_echo",
                action_pattern="echo",
                risk_threshold=RiskLevel.UNKNOWN,
                decision=PolicyDecision.ALLOW,
                priority=10,
            )
        ],
        kill_switch=kill,
    )

    # Before activation: echo is allowed by the explicit rule.
    call = ToolCall(id=new_id("tc"), tool_name="echo", arguments={"message": "hi"})
    verdict_before = await firewall.check(call, context=None)
    assert verdict_before.allowed is True

    # Activate the kill switch.
    await kill.activate(reason="emergency stop", activated_by="operator")
    assert kill.is_active() is True

    # After activation: the SAME call is now denied — kill switch overrides rules.
    verdict_after = await firewall.check(call, context=None)
    assert verdict_after.allowed is False
    assert verdict_after.decision == PolicyDecision.DENY
    assert verdict_after.kill_switch_active is True
    assert verdict_after.reason == "kill_switch_active"
