"""Regression test: same call_id + different tool → both execute.

Verifies that the cognitive-loop dedup is scoped to (call_id, tool_name),
not just call_id. A call_id reused with a different tool is NOT a duplicate.
"""
from __future__ import annotations

import pytest
from albra_seed import (
    Agent, AgentIdentity, Runtime, SeedConfig,
    AutonomyLevel, RiskLevel,
)
from albra_seed.core.types import ToolCall, AgentResponse
from albra_seed.core.base import ToolContract
from albra_seed.providers.mock import MockProvider
from albra_seed.tools.infra_tools import EchoTool
from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule
from albra_seed.runtime.loop import CognitiveLoopConfig


class _SpyTool(EchoTool):
    def __init__(self, name):
        super().__init__()
        self.execute_count = 0
        self.contract = ToolContract(
            name=name, description=f"spy {name}",
            input_schema={"message": {"type": "string"}}, output_schema={},
            risk_level=RiskLevel.LOW, permissions=[], timeout_seconds=30,
            retry_policy={"max": 0}, evidence_required=False,
            is_dry_run_capable=False, is_external=False, availability="available",
        )
    async def execute(self, arguments, *, context=None, dry_run=False):
        self.execute_count += 1
        return await super().execute(arguments, context=context, dry_run=dry_run)


class _TwoToolProvider(MockProvider):
    """Iter 1: tool_a with call_id='X'. Iter 2: tool_b with call_id='X'."""
    def __init__(self):
        super().__init__()
        self._iter = 0
    async def complete(self, messages, **kwargs):
        self._iter += 1
        ctx = kwargs.get("context")
        eid = ctx.execution_id if ctx else "x"
        cid = ctx.correlation_id if ctx else "y"
        if self._iter == 1:
            return AgentResponse(
                content="tool A",
                execution_id=eid, correlation_id=cid,
                tool_calls=[ToolCall(id="shared-id", tool_name="tool_a", arguments={"message": "a"})],
            )
        elif self._iter == 2:
            return AgentResponse(
                content="tool B",
                execution_id=eid, correlation_id=cid,
                tool_calls=[ToolCall(id="shared-id", tool_name="tool_b", arguments={"message": "b"})],
            )
        return AgentResponse(content="done", execution_id=eid, correlation_id=cid)


@pytest.mark.asyncio
async def test_same_call_id_different_tool_both_execute():
    """Same call_id + different tool_name → both must execute.
    
    The dedup is scoped to (call_id, tool_name), NOT just call_id.
    A call_id reused with a different tool is a new operation.
    """
    runtime = Runtime(config=SeedConfig())
    await runtime.startup()

    spy_a = _SpyTool("tool_a")
    spy_b = _SpyTool("tool_b")
    runtime.register_tool(spy_a)
    runtime.register_tool(spy_b)

    runtime.register_provider(_TwoToolProvider())

    fw = ActionFirewall()
    fw.add_rule(FirewallRule(action_pattern="tool_a", actor_pattern="*", decision="allow",
                             risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0))
    fw.add_rule(FirewallRule(action_pattern="tool_b", actor_pattern="*", decision="allow",
                             risk_threshold=RiskLevel.UNKNOWN, autonomy_required=0))
    runtime.register("firewall", fw)

    policy = PolicyEngine()
    policy.add_rule(PolicyRule(name="a", action_pattern="tool_a", actor_pattern="*",
                               risk_threshold=RiskLevel.UNKNOWN, autonomy_required=AutonomyLevel.L0_OBSERVE,
                               decision="allow", priority=10))
    policy.add_rule(PolicyRule(name="b", action_pattern="tool_b", actor_pattern="*",
                               risk_threshold=RiskLevel.UNKNOWN, autonomy_required=AutonomyLevel.L0_OBSERVE,
                               decision="allow", priority=10))
    runtime.register("policy_engine", policy)

    identity = AgentIdentity(name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE)
    agent = Agent(identity, runtime, cognitive_loop_config=CognitiveLoopConfig(
        max_iterations=5, max_tool_calls=10, max_calls_per_tool=10,
    ))

    response = await agent.run("test dedup scope")

    # Both tools executed despite sharing the same call_id
    assert spy_a.execute_count == 1, f"tool_a should execute once, got {spy_a.execute_count}"
    assert spy_b.execute_count == 1, f"tool_b should execute once, got {spy_b.execute_count}"

    await runtime.shutdown()
