"""Cognitive mode tests — linear (default) vs cognitive (opt-in).

H-09 — explicitly verify the Agent's two operating modes:

1. **LINEAR** (default, ``cognitive_loop_config=None``): a single
   REASON → PLAN → CHECK_PERMISSIONS → EXECUTE → OBSERVE → VALIDATE →
   RECORD → RESPOND cycle. The provider is called ONCE regardless of
   whether the draft response contained ``tool_calls`` — there is no
   re-reasoning over tool observations.

2. **COGNITIVE** (opt-in via ``cognitive_loop_config=CognitiveLoopConfig()``):
   iterates REASON → PLAN → CHECK_PERMISSIONS → EXECUTE → OBSERVE as
   long as the draft still contains ``tool_calls`` and the budget
   limits (``max_iterations``, ``max_tool_calls``, ``max_time_seconds``,
   ``max_context_messages``, ``max_calls_per_tool``) are not exceeded.
   Tool observations from prior iterations are surfaced to the
   provider on subsequent REASON passes as ``role=SYSTEM`` messages
   (DATA — never INSTRUCTIONS).

The tests are deterministic (Mock providers, no real LLM) and
explicitly verify the provider's call count, the
``RunContext.cognitive_iterations`` counter, and authorization
behavior in BOTH modes.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from albra_seed import (
    Agent,
    AgentIdentity,
    AutonomyLevel,
    RiskLevel,
    Runtime,
    SeedConfig,
)
from albra_seed.core.base import LLMProvider, Tool, ToolContract
from albra_seed.core.types import (
    AgentResponse,
    Message,
    RunContext,
    ToolCall,
    ToolResult,
)
from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule
from albra_seed.runtime.loop import CognitiveLoopConfig
from albra_seed.tools.infra_tools import EchoTool

# =====================================================================
# Mock components
# =====================================================================


class _ScriptedProvider(LLMProvider):
    """Provider that returns scripted responses in sequence.

    Each ``complete`` call advances the script. Items may be:

      * a ``str`` → ``AgentResponse`` with no ``tool_calls`` (loop exits).
      * a ``list[ToolCall]`` → ``AgentResponse`` carrying those calls.
      * an ``AgentResponse`` → returned verbatim.

    After the script is exhausted, subsequent calls return a plain
    response with no ``tool_calls`` so the cognitive loop terminates
    (defensive — prevents infinite loops if a test forgets to terminate
    the script).
    """

    name = "scripted"
    is_mock = True
    is_external = False

    def __init__(self, script: list[Any]) -> None:
        self._script: list[Any] = list(script)
        self._call_count: int = 0
        self.received_messages: list[list[Message]] = []

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
        self._call_count += 1
        # Snapshot the messages list — not the live reference — so
        # subsequent mutations by the agent loop don't retroactively
        # change what we recorded.
        self.received_messages.append(list(messages))
        exec_id = context.execution_id if context else "exec"
        corr_id = context.correlation_id if context else "corr"
        if self._call_count <= len(self._script):
            item = self._script[self._call_count - 1]
        else:
            item = "script-exhausted"
        if isinstance(item, AgentResponse):
            return item
        if isinstance(item, list):
            return AgentResponse(
                content="tool-calls",
                execution_id=exec_id,
                correlation_id=corr_id,
                tool_calls=list(item),
            )
        return AgentResponse(
            content=str(item),
            execution_id=exec_id,
            correlation_id=corr_id,
        )

    async def health(self) -> bool:
        return True

    @property
    def call_count(self) -> int:
        return self._call_count


class _AlwaysToolCallsProvider(LLMProvider):
    """Provider that ALWAYS returns a fresh-id tool_call on every call.

    Used by the ``max_iterations`` test to drive the cognitive loop to
    its iteration cap without triggering the no-new-work detector
    (which terminates the loop when the same ids re-appear).
    """

    name = "always-tool-calls"
    is_mock = True

    def __init__(self, tool_name: str = "echo") -> None:
        self._tool_name = tool_name
        self._call_count = 0

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
        self._call_count += 1
        exec_id = context.execution_id if context else "exec"
        corr_id = context.correlation_id if context else "corr"
        return AgentResponse(
            content=f"iter-{self._call_count}",
            execution_id=exec_id,
            correlation_id=corr_id,
            tool_calls=[
                ToolCall(
                    id=f"tc-{self._call_count}",
                    tool_name=self._tool_name,
                    arguments={"message": "hi"},
                )
            ],
        )

    async def health(self) -> bool:
        return True

    @property
    def call_count(self) -> int:
        return self._call_count


class _CounterTool(Tool):
    """Tool that counts how many times its ``execute`` was called."""

    def __init__(self, name: str = "counter") -> None:
        self.contract = ToolContract(
            name=name,
            description="Counter — counts execute() invocations.",
            risk_level=RiskLevel.LOW,
        )
        self.execute_count: int = 0

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        self.execute_count += 1
        return ToolResult(
            call_id=arguments.get("_call_id", f"counter-{self.execute_count}"),
            success=True,
            output={"count": self.execute_count, "args": dict(arguments)},
        )


# =====================================================================
# Helpers
# =====================================================================


_DEFAULT_COGNITIVE_CONFIG: CognitiveLoopConfig = CognitiveLoopConfig()


def _build_runtime(
    *,
    provider: LLMProvider,
    tools: list[Tool] | None = None,
    firewall_allow: list[str] | None = None,
    cognitive_config: CognitiveLoopConfig | None | str = "default",
    autonomy: AutonomyLevel = AutonomyLevel.L2_EXECUTE_SAFE,
) -> tuple[Runtime, Agent]:
    """Wire a Runtime + Agent with a permissive firewall (default-allow).

    Both gates (firewall AND policy engine) are configured to ALLOW
    the supplied tool names. Without this, the security perimeter
    DEFAULT-DENIES every tool call (the cognitive loop would never
    actually execute any tools).

    ``cognitive_config`` semantics:
      * ``"default"`` (sentinel default): use ``_DEFAULT_COGNITIVE_CONFIG``
        — a module-level singleton ``CognitiveLoopConfig()`` instance
        (cognitive mode active with spec defaults).
      * ``None``: explicitly select the LINEAR loop (no cognitive iteration).
      * a ``CognitiveLoopConfig`` instance: pass it through as-is.

    Using a sentinel (rather than the default ``CognitiveLoopConfig()``)
    avoids the B008 (mutable-default-argument) lint warning while
    preserving the default-opt-in cognitive behavior.

    ``firewall_allow`` semantics:
      * ``None`` (default): allow every REGISTERED tool name.
      * ``[]`` (explicit empty list): allow NOTHING — every tool is
        denied by DEFAULT-DENY. Tests that need this can pass ``[]``
        explicitly.
      * ``["echo", "counter"]``: allow exactly those tool names.
    """
    if cognitive_config == "default":
        cognitive_config = _DEFAULT_COGNITIVE_CONFIG
    runtime = Runtime(config=SeedConfig())
    asyncio.get_event_loop()  # touch the loop to ensure one exists
    runtime.register_provider(provider)
    for tool in (tools or []):
        runtime.register_tool(tool)

    if firewall_allow is None:
        allow = [t.contract.name for t in (tools or [])]
    else:
        allow = list(firewall_allow)
    fw = ActionFirewall()
    for tool_name in allow:
        fw.add_rule(
            FirewallRule(
                action_pattern=tool_name,
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
    runtime.register("firewall", fw)

    policy = PolicyEngine()
    for tool_name in allow:
        policy.add_rule(
            PolicyRule(
                name=f"allow_{tool_name}",
                action_pattern=tool_name,
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=AutonomyLevel.L0_OBSERVE,
                decision="allow",
                priority=10,
            )
        )
    runtime.register("policy_engine", policy)

    identity = AgentIdentity(name="cognitive-modes-test-agent", autonomy_level=autonomy)
    agent = Agent(identity, runtime, cognitive_loop_config=cognitive_config)
    return runtime, agent


async def _run_with_startup(
    runtime: Runtime, agent: Agent, prompt: str
) -> AgentResponse:
    """startup → run → shutdown, returning the AgentResponse."""
    await runtime.startup()
    try:
        return await agent.run(prompt)
    finally:
        await runtime.shutdown()


def _ctx(agent: Agent) -> RunContext:
    """Pull the RunContext from the agent's state (set by Agent.run)."""
    ctx = agent.state.current_context
    assert ctx is not None, "Agent.run did not populate state.current_context"
    return ctx


# =====================================================================
# Test class — explicit mode defaults
# =====================================================================


class TestCognitiveModeDefaults:
    """Explicitly verify the two cognitive modes are documented + enforced."""

    @pytest.mark.asyncio
    async def test_default_mode_is_linear(self) -> None:
        """Agent.run() without cognitive_loop_config → single iteration.

        The default ``Agent.__init__`` is ``cognitive_loop_config=None``
        which selects the LEGACY LINEAR loop. Even when the provider
        returns tool_calls (which would loop in cognitive mode), the
        linear loop runs the 10 canonical phases EXACTLY ONCE —
        ``provider.call_count == 1``.

        The ``RunContext.cognitive_iterations`` field is annotated as 1
        for the linear path (the single REASON→EXECUTE→OBSERVE pass),
        while ``cognitive_tool_calls`` reflects the count of tool_calls
        produced by the draft (1 in this case).
        """
        echo = EchoTool()
        # The provider returns tool_calls — in COGNITIVE mode this would
        # loop again. In LINEAR mode, the loop terminates after a single
        # pass and the tool is executed ONCE.
        provider = _ScriptedProvider(
            [
                [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})],
                # Second item would only be reached if the loop iterated again.
                "linear-mode-terminating-response",
            ]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[echo],
            cognitive_config=None,  # LINEAR (default)
        )
        response = await _run_with_startup(runtime, agent, "hello")
        # Linear mode: provider called exactly ONCE (no re-reasoning).
        assert provider.call_count == 1, (
            f"linear mode should call provider once; got {provider.call_count}"
        )
        ctx = _ctx(agent)
        # The linear-loop annotator records a single iteration.
        assert ctx.cognitive_iterations == 1
        # The provider's single draft contained one tool_call.
        assert ctx.cognitive_tool_calls == 1
        # No cognitive-loop limit warnings (linear mode never enters the
        # inner loop).
        assert not any(
            w.startswith("cognitive_loop_limit_reached") for w in ctx.warnings
        ), f"unexpected limit warning in linear mode: {ctx.warnings!r}"
        # The response was composed from the FIRST draft (the second
        # script item was never read).
        assert response.content == "tool-calls"

    @pytest.mark.asyncio
    async def test_cognitive_mode_iterates(self) -> None:
        """Agent.run(cognitive_loop_config=CognitiveLoopConfig()) → multiple iterations.

        Iter 1 returns a tool_call; iter 2 returns a plain response. The
        cognitive loop runs EXACTLY TWO iterations — the provider is
        called TWICE. The tool is executed ONCE (during iter 1's
        EXECUTE phase); iter 2 has no tool_calls so EXECUTE produces no
        results.
        """
        counter = _CounterTool(name="counter")
        provider = _ScriptedProvider(
            [
                [ToolCall(id="tc-1", tool_name="counter", arguments={})],
                "done-after-iter-1",
            ]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[counter],
            cognitive_config=CognitiveLoopConfig(),
        )
        response = await _run_with_startup(runtime, agent, "do work")
        assert response.content == "done-after-iter-1"
        ctx = _ctx(agent)
        assert ctx.cognitive_iterations == 2
        assert ctx.cognitive_tool_calls == 1  # one tool_call produced (iter 1)
        assert provider.call_count == 2
        # The tool was actually executed (not deduped — first time seen).
        assert counter.execute_count == 1

    @pytest.mark.asyncio
    async def test_cognitive_mode_respects_max_iterations(self) -> None:
        """max_iterations=2 → stops after 2 iterations even if more work.

        The provider ALWAYS returns a fresh-id tool_call (so the
        no-new-work detector cannot fire). The cognitive loop runs
        EXACTLY 2 iterations then terminates with a
        ``cognitive_loop_limit_reached:max_iterations`` warning. The
        provider is called 2 times.
        """
        counter = _CounterTool(name="counter")
        provider = _AlwaysToolCallsProvider(tool_name="counter")
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[counter],
            cognitive_config=CognitiveLoopConfig(
                max_iterations=2, max_tool_calls=1000, max_calls_per_tool=100
            ),
        )
        await _run_with_startup(runtime, agent, "loop")
        ctx = _ctx(agent)
        assert ctx.cognitive_iterations == 2
        assert provider.call_count == 2
        assert any(
            "cognitive_loop_limit_reached:max_iterations" in w
            for w in ctx.warnings
        ), f"expected max_iterations warning in {ctx.warnings!r}"

    @pytest.mark.asyncio
    async def test_linear_mode_denied_tool_still_responds(self) -> None:
        """Linear mode: denied tool → response with error, not crash.

        A denied tool produces a failed ``ToolResult`` (NOT an exception).
        The loop continues to OBSERVE → VALIDATE → RECORD → RESPOND and
        composes a normal AgentResponse. The provider is called ONCE.
        """
        echo = EchoTool()
        provider = _ScriptedProvider(
            [
                [ToolCall(id="tc-denied", tool_name="echo", arguments={"message": "hi"})],
            ]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[echo],
            firewall_allow=[],  # NO allow rules → DEFAULT-DENY
            cognitive_config=None,  # LINEAR
        )
        response = await _run_with_startup(runtime, agent, "echo")
        # The provider was called exactly once (linear mode).
        assert provider.call_count == 1
        # The tool was DENIED — the result is a failed ToolResult, NOT a crash.
        assert response.tool_results, "denied tool must produce a ToolResult"
        assert response.tool_results[0].success is False
        # The denial surfaces in the error string.
        assert "deny" in (response.tool_results[0].error or "").lower() or (
            "blocked" in (response.tool_results[0].error or "").lower()
        )
        ctx = _ctx(agent)
        # Linear-loop annotation: 1 iteration, 0 tool_calls dispatched
        # (the tool was denied, so it was never dispatched).
        assert ctx.cognitive_iterations == 1
        assert ctx.cognitive_tool_calls == 0  # denied → not dispatched

    @pytest.mark.asyncio
    async def test_cognitive_mode_denied_tool_still_iterates(self) -> None:
        """Cognitive mode: denied tool → tool result is data → can continue.

        Iter 1's tool is denied (no allow rule for "counter"). The
        failed ToolResult is recorded in the observations stream. The
        cognitive loop does NOT crash; it continues to the next REASON
        pass. The provider's iter 2 script returns a plain response,
        terminating the loop.

        This proves the cognitive loop treats denial as DATA (a
        ToolResult to be observed) rather than as a control-flow
        exception that aborts the loop.
        """
        counter = _CounterTool(name="counter")
        provider = _ScriptedProvider(
            [
                [ToolCall(id="tc-denied-1", tool_name="counter", arguments={})],
                "done-after-denial",
            ]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[counter],
            firewall_allow=[],  # NO allow rules → DEFAULT-DENY
            cognitive_config=CognitiveLoopConfig(),
        )
        response = await _run_with_startup(runtime, agent, "do work")
        # Provider was called TWICE — the cognitive loop iterated even
        # though iter 1's tool was DENIED.
        assert provider.call_count == 2
        ctx = _ctx(agent)
        assert ctx.cognitive_iterations == 2
        # The denial produced a failed ToolResult (not a crash).
        assert response.tool_results, "denied tool must produce a ToolResult"
        assert response.tool_results[0].success is False
        # The tool's execute() was NEVER called (denied before execution).
        assert counter.execute_count == 0
        # The loop terminated via the natural "no tool_calls in iter 2"
        # path — NOT via a budget limit.
        assert not any(
            w.startswith("cognitive_loop_limit_reached") for w in ctx.warnings
        ), f"unexpected limit warning: {ctx.warnings!r}"


# =====================================================================
# Test class — both modes produce valid responses
# =====================================================================


class TestBothModesProduceValidResponses:
    """Both linear and cognitive modes return well-formed AgentResponse."""

    @pytest.mark.asyncio
    async def test_linear_mode_response_shape(self) -> None:
        """Linear mode: AgentResponse carries content + execution_id."""
        provider = _ScriptedProvider(["linear-response"])
        runtime, agent = _build_runtime(
            provider=provider,
            cognitive_config=None,  # LINEAR
        )
        response = await _run_with_startup(runtime, agent, "hello")
        assert response.content == "linear-response"
        assert response.execution_id
        assert response.correlation_id
        # No tools were called.
        assert response.tool_calls == []
        assert response.tool_results == []

    @pytest.mark.asyncio
    async def test_cognitive_mode_response_shape(self) -> None:
        """Cognitive mode: AgentResponse carries content + execution_id."""
        provider = _ScriptedProvider(["cognitive-response"])
        runtime, agent = _build_runtime(
            provider=provider,
            cognitive_config=CognitiveLoopConfig(),
        )
        response = await _run_with_startup(runtime, agent, "hello")
        assert response.content == "cognitive-response"
        assert response.execution_id
        assert response.correlation_id
        # No tools were called.
        assert response.tool_calls == []
        assert response.tool_results == []


# =====================================================================
# Test class — both modes respect authorization
# =====================================================================


class TestBothModesRespectAuthorization:
    """Both modes enforce DEFAULT-DENY when no security perimeter allows."""

    @pytest.mark.asyncio
    async def test_linear_mode_denies_unauthorized_tool(self) -> None:
        """Linear mode: no allow rules → tool denied."""
        echo = EchoTool()
        provider = _ScriptedProvider(
            [
                [ToolCall(id="tc-x", tool_name="echo", arguments={"message": "hi"})],
            ]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[echo],
            firewall_allow=[],  # no allow rules
            cognitive_config=None,  # LINEAR
        )
        response = await _run_with_startup(runtime, agent, "echo")
        assert response.tool_results
        assert response.tool_results[0].success is False

    @pytest.mark.asyncio
    async def test_cognitive_mode_denies_unauthorized_tool(self) -> None:
        """Cognitive mode: no allow rules → tool denied (still iterates once)."""
        echo = EchoTool()
        provider = _ScriptedProvider(
            [
                [ToolCall(id="tc-x", tool_name="echo", arguments={"message": "hi"})],
                "done",
            ]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[echo],
            firewall_allow=[],  # no allow rules
            cognitive_config=CognitiveLoopConfig(),
        )
        response = await _run_with_startup(runtime, agent, "echo")
        # The cognitive loop iterated (denial is DATA, not a crash).
        assert response.tool_results
        assert response.tool_results[0].success is False

    @pytest.mark.asyncio
    async def test_linear_mode_allows_authorized_tool(self) -> None:
        """Linear mode: allow rules → tool executed."""
        echo = EchoTool()
        provider = _ScriptedProvider(
            [
                [ToolCall(id="tc-y", tool_name="echo", arguments={"message": "hi"})],
            ]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[echo],
            firewall_allow=["echo"],
            cognitive_config=None,  # LINEAR
        )
        response = await _run_with_startup(runtime, agent, "echo")
        assert response.tool_results
        assert response.tool_results[0].success is True

    @pytest.mark.asyncio
    async def test_cognitive_mode_allows_authorized_tool(self) -> None:
        """Cognitive mode: allow rules → tool executed."""
        echo = EchoTool()
        provider = _ScriptedProvider(
            [
                [ToolCall(id="tc-z", tool_name="echo", arguments={"message": "hi"})],
                "done",
            ]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[echo],
            firewall_allow=["echo"],
            cognitive_config=CognitiveLoopConfig(),
        )
        response = await _run_with_startup(runtime, agent, "echo")
        assert response.tool_results
        assert response.tool_results[0].success is True


# =====================================================================
# Test class — per-call override
# =====================================================================


class TestCognitiveModePerCallOverride:
    """Agent.run(cognitive_loop_config=...) overrides construction-time config."""

    @pytest.mark.asyncio
    async def test_run_can_override_to_cognitive(self) -> None:
        """An Agent constructed in LINEAR mode can opt into COGNITIVE per-call.

        ``Agent.run(cognitive_loop_config=CognitiveLoopConfig())`` overrides
        the Agent's construction-time config (``None`` → linear) for
        THIS run only.
        """
        echo = EchoTool()
        provider = _ScriptedProvider(
            [
                [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})],
                "done",
            ]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[echo],
            cognitive_config=None,  # constructed LINEAR
        )
        await runtime.startup()
        try:
            # Override to COGNITIVE for this call only.
            await agent.run(
                "do work",
                cognitive_loop_config=CognitiveLoopConfig(),
            )
        finally:
            await runtime.shutdown()
        # Provider called TWICE — the cognitive loop iterated.
        assert provider.call_count == 2
        ctx = _ctx(agent)
        assert ctx.cognitive_iterations == 2

    @pytest.mark.asyncio
    async def test_run_with_none_falls_back_to_construction_config(self) -> None:
        """``Agent.run(cognitive_loop_config=None)`` → construction-time config wins.

        The per-call override is OPT-IN only: passing a non-``None``
        ``CognitiveLoopConfig`` overrides the Agent's defaults, but
        passing ``None`` (the default) falls back to the Agent's
        construction-time config. There is no per-call opt-OUT of the
        cognitive loop — an Agent constructed in cognitive mode stays
        in cognitive mode unless explicitly re-constructed.

        This verifies the contract documented in ``Agent.run()``:

            ``cognitive_loop_config : CognitiveLoopConfig | None``
                Optional per-call override of the cognitive-loop limits.
                When ``None`` (the default), the Agent's
                ``cognitive_loop_config`` (set at construction) is used —
                itself ``None`` falls back to the legacy linear loop.
                Passing a non-``None`` value here activates the cognitive
                loop for THIS run only, overriding the Agent's defaults.
        """
        echo = EchoTool()
        provider = _ScriptedProvider(
            [
                [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})],
                "done-after-iter-1",
            ]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[echo],
            cognitive_config=CognitiveLoopConfig(),  # constructed COGNITIVE
        )
        await runtime.startup()
        try:
            # Passing None (default) → construction-time config (cognitive).
            await agent.run(
                "do work",
                cognitive_loop_config=None,
            )
        finally:
            await runtime.shutdown()
        # The Agent was constructed in cognitive mode; passing None to
        # ``run()`` falls back to the construction-time config (cognitive)
        # — the loop iterated twice (iter 1 produced a tool_call; iter 2
        # returned a plain response).
        assert provider.call_count == 2
        ctx = _ctx(agent)
        assert ctx.cognitive_iterations == 2


# =====================================================================
# Test class — mode selection invariant (config is the only switch)
# =====================================================================


class TestModeSelectionInvariant:
    """The ONLY way to enable the cognitive loop is via cognitive_loop_config.

    There is no other toggle (no env var, no runtime component, no
    class attribute) that activates the cognitive loop. The construction-
    time ``cognitive_loop_config`` argument and the per-call
    ``cognitive_loop_config`` argument to ``Agent.run()`` are the sole
    switches.
    """

    def test_agent_default_construction_is_linear(self) -> None:
        """Agent(identity, runtime) without cognitive_loop_config → linear.

        ``Agent.cognitive_loop_config`` defaults to ``None`` (the
        LINEAR mode). There is no implicit opt-in.
        """
        runtime = Runtime(config=SeedConfig())
        identity = AgentIdentity(name="linear-default-agent")
        agent = Agent(identity, runtime)
        assert agent.cognitive_loop_config is None

    def test_agent_construction_with_config_is_cognitive(self) -> None:
        """Agent(identity, runtime, cognitive_loop_config=cfg) → cognitive."""
        runtime = Runtime(config=SeedConfig())
        identity = AgentIdentity(name="cognitive-agent")
        cfg = CognitiveLoopConfig()
        agent = Agent(identity, runtime, cognitive_loop_config=cfg)
        assert agent.cognitive_loop_config is cfg

    def test_cognitive_loop_config_defaults_are_safe(self) -> None:
        """Default CognitiveLoopConfig has non-zero limits (no infinite loops).

        A misconfigured or default-constructed config MUST NEVER allow
        an infinite loop. All five limits must be > 0.

        This is the documented contract from the COGNITIVE-CYCLE task
        and is also verified by the existing
        ``test_default_cognitive_loop_config_has_safe_limits`` test in
        ``tests/unit/test_cognitive_cycle.py``. We re-verify it here so
        the H-09 cognitive-modes test file is self-contained.
        """
        cfg = CognitiveLoopConfig()
        assert cfg.max_iterations > 0
        assert cfg.max_tool_calls > 0
        assert cfg.max_time_seconds > 0
        assert cfg.max_context_messages > 0
        assert cfg.max_calls_per_tool > 0
        # The defaults match the values specified in the COGNITIVE-CYCLE task.
        assert cfg.max_iterations == 5
        assert cfg.max_tool_calls == 20
        assert cfg.max_time_seconds == 120.0
        assert cfg.max_context_messages == 50
        assert cfg.max_calls_per_tool == 5
