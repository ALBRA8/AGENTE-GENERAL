"""Integration test for H-14: denied call in cognitive loop + retry with same ID.

This test exercises the FULL agent loop (not just ToolExecutor directly):
1. Agent runs with cognitive loop enabled
2. First iteration: provider emits a tool_call with id="test-dedup-1"
3. The firewall DENIES the call (no allow-rule for the tool)
4. Second iteration: provider emits the SAME call_id with a different tool
   that IS allowed
5. Verify: the denied call did NOT contaminate executed_call_ids
6. Verify: the spy tool's execute_count reflects only successful executions
"""
from __future__ import annotations

import pytest
from albra_seed import (
    Agent, AgentIdentity, Runtime, SeedConfig,
    AutonomyLevel, RiskLevel, RunContext,
)
from albra_seed.core.types import ToolCall, AgentResponse, Message, Role
from albra_seed.core.base import ToolContract
from albra_seed.providers.mock import MockProvider
from albra_seed.tools.infra_tools import EchoTool
from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule
from albra_seed.runtime.loop import CognitiveLoopConfig


class _SpyTool(EchoTool):
    """EchoTool that tracks every execute() call."""
    def __init__(self, name="spy"):
        super().__init__()
        self.execute_count = 0
        self.contract = ToolContract(
            name=name, description="spy tool",
            input_schema={"message": {"type": "string"}}, output_schema={},
            risk_level=RiskLevel.LOW, permissions=[], timeout_seconds=30,
            retry_policy={"max": 0}, evidence_required=False,
            is_dry_run_capable=False, is_external=False, availability="available",
        )

    async def execute(self, arguments, *, context=None, dry_run=False):
        self.execute_count += 1
        return await super().execute(arguments, context=context, dry_run=dry_run)


class _TwoPhaseProvider(MockProvider):
    """Provider that emits different tool_calls on each iteration.
    
    Iteration 1: emits a call to 'denied_tool' (will be denied by firewall)
    Iteration 2: emits a call to 'spy' (allowed by firewall) with the SAME call_id
    Iteration 3+: no tool_calls (done)
    """
    def __init__(self, call_id: str):
        super().__init__()
        self._call_count = 0
        self._call_id = call_id

    async def complete(self, messages, **kwargs):
        self._call_count += 1
        ctx = kwargs.get("context")
        exec_id = ctx.execution_id if ctx else "x"
        corr_id = ctx.correlation_id if ctx else "y"

        if self._call_count == 1:
            # First iteration: emit a tool_call that will be DENIED
            return AgentResponse(
                content="trying denied tool",
                execution_id=exec_id,
                correlation_id=corr_id,
                tool_calls=[ToolCall(
                    id=self._call_id,
                    tool_name="denied_tool",
                    arguments={"message": "blocked"},
                )],
            )
        elif self._call_count == 2:
            # Second iteration: emit the SAME call_id but for an ALLOWED tool
            return AgentResponse(
                content="retrying with allowed tool",
                execution_id=exec_id,
                correlation_id=corr_id,
                tool_calls=[ToolCall(
                    id=self._call_id,
                    tool_name="spy",
                    arguments={"message": "allowed"},
                )],
            )
        # Third+ iteration: no more tool calls
        return AgentResponse(
            content="done",
            execution_id=exec_id,
            correlation_id=corr_id,
        )


@pytest.mark.asyncio
async def test_denied_call_does_not_contaminate_dedup_set():
    """H-14 integration test: a denied call in iteration 1 must NOT prevent
    execution of the same call_id in iteration 2 with a different (allowed) tool.
    
    Verifies:
    1. First call is denied (denied_tool has no allow-rule in firewall)
    2. Second call with SAME call_id executes (spy tool is allowed)
    3. Spy tool execute_count == 1 (only the successful execution)
    4. The denied call_id did NOT contaminate executed_call_ids
    """
    runtime = Runtime(config=SeedConfig())
    await runtime.startup()

    # Register spy tool (allowed)
    spy = _SpyTool("spy")
    runtime.register_tool(spy)

    # Register a denied_tool (same interface, but firewall won't allow it)
    denied_spy = _SpyTool("denied_tool")
    runtime.register_tool(denied_spy)

    # Provider: first iteration emits denied_tool, second emits spy (same call_id)
    call_id = "test-dedup-h14"
    provider = _TwoPhaseProvider(call_id)
    runtime.register_provider(provider)

    # Firewall: allow ONLY "spy", NOT "denied_tool"
    fw = ActionFirewall()
    fw.add_rule(FirewallRule(
        action_pattern="spy", actor_pattern="*", decision="allow",
        risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0,
    ))
    runtime.register("firewall", fw)

    # Policy: allow "spy", deny "denied_tool"
    policy = PolicyEngine()
    policy.add_rule(PolicyRule(
        name="allow_spy", action_pattern="spy", actor_pattern="*",
        risk_threshold=RiskLevel.UNKNOWN, autonomy_required=AutonomyLevel.L0_OBSERVE,
        decision="allow", priority=10,
    ))
    policy.add_rule(PolicyRule(
        name="deny_denied", action_pattern="denied_tool", actor_pattern="*",
        risk_threshold=RiskLevel.UNKNOWN, autonomy_required=AutonomyLevel.L5_SELF_IMPROVE,
        decision="deny", priority=20,
    ))
    runtime.register("policy_engine", policy)

    identity = AgentIdentity(
        name="h14-test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
    )
    agent = Agent(identity, runtime, cognitive_loop_config=CognitiveLoopConfig(
        max_iterations=5, max_tool_calls=10, max_calls_per_tool=10,
    ))

    response = await agent.run("test H-14")

    # 1. First call was denied (denied_tool never executed)
    assert denied_spy.execute_count == 0, (
        f"denied_tool should NOT have been executed, got {denied_spy.execute_count}"
    )

    # 2. Second call with SAME call_id executed (spy tool ran)
    assert spy.execute_count == 1, (
        f"spy should have executed exactly once, got {spy.execute_count}"
    )

    # 3. Response should contain results from both iterations
    assert response.tool_results is not None
    assert len(response.tool_results) >= 2, (
        f"Expected at least 2 tool_results, got {len(response.tool_results)}"
    )

    # 4. First result should be a failure (denied)
    first_result = response.tool_results[0]
    assert not first_result.success, (
        f"First call should have been denied, got success={first_result.success}"
    )

    # 5. Second result should be success (allowed)
    second_result = response.tool_results[1]
    assert second_result.success, (
        f"Second call should have succeeded, got success={second_result.success}"
    )

    await runtime.shutdown()
