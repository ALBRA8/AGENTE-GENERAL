"""Unit tests for the ALBRA runtime subpackage (F2-A).

These tests exercise the runtime, lifecycle, and agent loop without
requiring real LLM providers, real security components, or any external
service. Mock components are constructed inline.
"""

from __future__ import annotations

import asyncio

import pytest

from albra_seed.core.base import (
    LLMProvider,
    MemoryRecord,
    MemoryStore,
    Tool,
    ToolContract,
)
from albra_seed.core.config import SeedConfig
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import (
    AgentResponse,
    AutonomyLevel,
    Message,
    Role,
    RunContext,
    Session,
)
from albra_seed.runtime import (
    Agent,
    AgentLoop,
    AgentState,
    LifecycleError,
    LifecycleManager,
    LifecyclePhase,
    PhaseEvent,
    PhaseStatus,
    Runtime,
)


# =====================================================================
# MOCK COMPONENTS
# =====================================================================


class MockProvider(LLMProvider):
    name = "mock"
    is_mock = True
    is_external = False

    def __init__(self, response: str = "mock-response") -> None:
        self._response = response
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
            content=self._response,
            execution_id=context.execution_id if context else "exec",
            correlation_id=context.correlation_id if context else "corr",
            confidence=0.9,
        )

    async def health(self) -> bool:
        return True


class EchoTool(Tool):
    def __init__(self) -> None:
        self.contract = ToolContract(
            name="echo", description="echoes input back", risk_level="low"
        )

    async def execute(
        self,
        arguments: dict,
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ):
        from albra_seed.core.types import ToolResult

        return ToolResult(
            call_id="test",
            success=True,
            output=arguments.get("text", ""),
        )


class InMemoryMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    async def write(self, record: MemoryRecord) -> str:
        self._records[record.record_id] = record
        return record.record_id

    async def read(self, record_id: str) -> MemoryRecord | None:
        return self._records.get(record_id)

    async def query(self, **kwargs) -> list[MemoryRecord]:
        return list(self._records.values())

    async def delete(self, record_id: str) -> bool:
        return self._records.pop(record_id, None) is not None

    async def count(self, *, agent_id: str, domain: str = "default") -> int:
        return len(self._records)


# =====================================================================
# FIXTURES
# =====================================================================


@pytest.fixture
def runtime() -> Runtime:
    return Runtime(SeedConfig(max_concurrent_sessions=2))


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider(response="hello from mock")


@pytest.fixture
def identity() -> AgentIdentity:
    return AgentIdentity(
        name="test-agent",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        providers=["mock"],
    )


# =====================================================================
# TEST 1 — runtime lifecycle starts and stops
# =====================================================================


async def test_runtime_lifecycle_starts_and_stops(runtime: Runtime) -> None:
    """startup() moves UNINITIALIZED → READY; shutdown() → STOPPED."""
    assert runtime.lifecycle.phase == LifecyclePhase.UNINITIALIZED
    await runtime.startup()
    assert runtime.lifecycle.phase == LifecyclePhase.READY
    await runtime.shutdown()
    assert runtime.lifecycle.phase == LifecyclePhase.STOPPED


# =====================================================================
# TEST 2 — runtime health returns dict
# =====================================================================


async def test_runtime_health_returns_dict(runtime: Runtime) -> None:
    """health() returns a dict with the canonical keys."""
    await runtime.startup()
    h = runtime.health()
    assert isinstance(h, dict)
    assert set(h.keys()) >= {"status", "phase", "components", "sessions", "uptime_seconds"}
    assert h["status"] in {"healthy", "degraded", "error"}
    assert "firewall" in h["components"]
    assert "provider_router" in h["components"]
    await runtime.shutdown()


# =====================================================================
# TEST 3 — agent loop executes RECEIVE → RESPOND
# =====================================================================


async def test_agent_loop_executes_receive_to_respond(
    runtime: Runtime, mock_provider: MockProvider, identity: AgentIdentity
) -> None:
    """The agent loop runs all 10 canonical phases in order."""
    await runtime.startup()
    runtime.register_provider(mock_provider)
    agent = Agent(identity, runtime)

    response = await agent.run("hello")
    assert response.content == "hello from mock"

    # Inspect the loop trace via context.outputs (recorded on context).
    states = await _last_trace_states(runtime, agent)
    assert states[0] == "RECEIVE"
    assert states[-1] == "RESPOND"
    # All 10 phases recorded.
    assert len(states) == 10
    await runtime.shutdown()


async def _last_trace_states(runtime: Runtime, agent: Agent) -> list[str]:
    """Return the list of phase names from the most recent execution trace."""
    # The trace is stashed on the RunContext outputs by _build_response.
    ctx = agent.state.current_context
    assert ctx is not None
    trace = ctx.outputs.get("trace", [])
    return [e["phase"] for e in trace]


# =====================================================================
# TEST 4 — agent returns an AgentResponse
# =====================================================================


async def test_agent_returns_agent_response(
    runtime: Runtime, mock_provider: MockProvider, identity: AgentIdentity
) -> None:
    """Agent.run() returns a properly constructed AgentResponse."""
    await runtime.startup()
    runtime.register_provider(mock_provider)
    agent = Agent(identity, runtime)

    response = await agent.run("hello world", )
    assert isinstance(response, AgentResponse)
    assert response.role == Role.ASSISTANT
    assert response.content == "hello from mock"
    assert response.execution_id  # non-empty
    assert response.correlation_id  # non-empty
    assert response.confidence == pytest.approx(0.9)
    # Session should be created and registered.
    assert runtime.active_session_count == 1
    await runtime.shutdown()


# =====================================================================
# TEST 5 — runtime rejects invalid state transitions
# =====================================================================


async def test_runtime_rejects_invalid_state_transition(runtime: Runtime) -> None:
    """Transitioning UNINITIALIZED → STOPPED directly must raise."""
    with pytest.raises(LifecycleError):
        runtime.lifecycle.transition(LifecyclePhase.STOPPED)
    # Phase unchanged.
    assert runtime.lifecycle.phase == LifecyclePhase.UNINITIALIZED


# =====================================================================
# TEST 6 — runtime enforces max_concurrent_sessions
# =====================================================================


async def test_runtime_enforces_max_concurrent_sessions(runtime: Runtime) -> None:
    """register_session() raises once max_concurrent_sessions is exceeded."""
    await runtime.startup()
    # config has max_concurrent_sessions=2 (see fixture).
    s1 = Session(agent_id="a")
    s2 = Session(agent_id="a")
    s3 = Session(agent_id="a")
    await runtime.register_session(s1)
    await runtime.register_session(s2)
    with pytest.raises(RuntimeError, match="max_concurrent_sessions"):
        await runtime.register_session(s3)
    assert runtime.active_session_count == 2
    # Removing one frees a slot.
    await runtime.remove_session(s1.session_id)
    await runtime.register_session(s3)
    assert runtime.active_session_count == 2
    await runtime.shutdown()


# =====================================================================
# TEST 7 — LifecycleManager records history and counts
# =====================================================================


def test_lifecycle_manager_records_history() -> None:
    """Transitions are appended to history and counted."""
    m = LifecycleManager()
    assert m.phase == LifecyclePhase.UNINITIALIZED
    m.transition(LifecyclePhase.INITIALIZING, reason="boot")
    m.transition(LifecyclePhase.READY, reason="ready")
    assert m.phase == LifecyclePhase.READY
    assert len(m.history) == 2
    assert m.history[0].phase == LifecyclePhase.INITIALIZING
    assert m.history[0].reason == "boot"
    counts = m.counts()
    assert counts["initializing"] == 1
    assert counts["ready"] == 1


# =====================================================================
# TEST 8 — AgentLoop captures phase timeouts and failures
# =====================================================================


async def test_agent_loop_captures_timeout_and_failure() -> None:
    """A slow phase times out; a raising phase is captured as FAILURE."""
    loop = AgentLoop(agent=None)  # type: ignore[arg-type]

    async def slow_phase(agent, ctx):
        await asyncio.sleep(5)
        return "never"

    ctx = {"execution_id": "exec-x"}
    ev = await loop.execute_phase("SLOW", slow_phase, ctx, timeout=0.05)
    assert ev.status == PhaseStatus.TIMEOUT
    assert ev.duration_ms > 0

    async def bad_phase(agent, ctx):
        raise ValueError("boom")

    ev2 = await loop.execute_phase("BAD", bad_phase, ctx, timeout=1.0)
    assert ev2.status == PhaseStatus.FAILURE
    assert "boom" in (ev2.error or "")


# =====================================================================
# TEST 9 — Agent uses tool registry and records tool use
# =====================================================================


async def test_agent_executes_tool_via_registry(
    runtime: Runtime, mock_provider: MockProvider, identity: AgentIdentity
) -> None:
    """A tool call in the draft response is dispatched through the registry."""
    from albra_seed.core.types import ToolCall

    class ToolCallingProvider(MockProvider):
        async def complete(self, messages, **kwargs):
            self.calls += 1
            ctx = kwargs.get("context")
            return AgentResponse(
                content="ok",
                execution_id=ctx.execution_id if ctx else "x",
                correlation_id=ctx.correlation_id if ctx else "y",
                tool_calls=[ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})],
            )

    await runtime.startup()
    runtime.register_provider(ToolCallingProvider())
    runtime.register_tool(EchoTool())

    agent = Agent(identity, runtime)
    response = await agent.run("echo me")
    assert response.tool_calls  # non-empty
    assert response.tool_results  # echo result captured
    assert response.tool_results[0].success is True
    assert response.tool_results[0].output == "hi"
    # RunContext records the tool use.
    assert "echo" in agent.state.current_context.tools_used
    await runtime.shutdown()


# =====================================================================
# TEST 10 — Runtime readiness depends on required components
# =====================================================================


async def test_runtime_readiness_requires_components(runtime: Runtime) -> None:
    """readiness() is False before startup; True after.

    Since GAP-5, `startup()` auto-registers a `provider_router` (with
    the always-on Mock) so CORE_ONLY birth works out of the box. The
    caller can still replace it with a custom router — caller wins.
    """
    # Before startup, phase is UNINITIALIZED → readiness False.
    assert runtime.readiness() is False
    await runtime.startup()
    # Auto-registered provider_router makes readiness True immediately.
    assert runtime.readiness() is True
    # Caller can replace with a custom router — readiness stays True.
    runtime.register("provider_router", MockProvider())
    assert runtime.readiness() is True
    await runtime.shutdown()
