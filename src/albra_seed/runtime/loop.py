"""Universal Agent Loop.

Encapsulates the canonical ALBRA execution cycle:

    RECEIVE → UNDERSTAND → REASON → PLAN → CHECK PERMISSIONS →
    EXECUTE → OBSERVE → VALIDATE → RECORD → RESPOND

The loop is *universal*: it has zero domain knowledge. Each phase is a
stub that delegates to registries on the Runtime (provider router, tool
registry, firewall, policy engine, approval engine, memory store, ...).
Specialization layers register concrete components; the loop orchestrates
them in the correct order with timing, error capture, and trace events.

Each phase execution:
  - is wrapped with `asyncio.wait_for` for per-phase timeouts,
  - emits a PhaseEvent (success / failure / timeout / skipped) to all
    registered event hooks,
  - records the event in the loop's trace (used to reconstruct RunContext).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now

if TYPE_CHECKING:  # pragma: no cover
    from albra_seed.runtime.agent import Agent


# Canonical phase order. Used as default `phases` list.
CANONICAL_PHASES: tuple[str, ...] = (
    "RECEIVE",
    "UNDERSTAND",
    "REASON",
    "PLAN",
    "CHECK_PERMISSIONS",
    "EXECUTE",
    "OBSERVE",
    "VALIDATE",
    "RECORD",
    "RESPOND",
)

# Phases that participate in the cognitive tool-reasoning loop. These are
# the phases that repeat each iteration when the agent re-reasons over
# accumulated tool observations. The pre-loop phases (RECEIVE, UNDERSTAND)
# run once before the loop, and the post-loop phases (VALIDATE, RECORD,
# RESPOND) run once after the loop terminates.
COGNITIVE_INNER_PHASES: tuple[str, ...] = (
    "REASON",
    "PLAN",
    "CHECK_PERMISSIONS",
    "EXECUTE",
    "OBSERVE",
)
COGNITIVE_PRE_PHASES: tuple[str, ...] = ("RECEIVE", "UNDERSTAND")
COGNITIVE_POST_PHASES: tuple[str, ...] = ("VALIDATE", "RECORD", "RESPOND")


class CognitiveLoopConfig(BaseModel):
    """Limits for the cognitive tool-reasoning loop.

    Prevents infinite loops, excessive iterations, and runaway costs.
    The cognitive loop runs REASON → PLAN → CHECK_PERMISSIONS →
    EXECUTE → OBSERVE repeatedly as long as the provider's draft response
    contains tool_calls. Each limit is checked at the END of every
    iteration (after OBSERVE). If any limit is reached, the loop
    terminates with a ``cognitive_loop_limit_reached`` warning on the
    RunContext, and the agent proceeds to VALIDATE → RECORD → RESPOND
    with whatever draft / observations were accumulated so far.

    All limits have safe non-zero defaults so a misconfigured agent
    never enters an infinite loop.
    """

    max_iterations: int = 5  # max reason→plan→execute→observe cycles
    max_tool_calls: int = 20  # max total tool calls per run
    max_time_seconds: float = 120.0  # wall-clock limit
    max_context_messages: int = 50  # max messages accumulated in context
    # H-07: per-tool cap. A single tool name cannot be invoked more than
    # this many times across ALL cognitive-loop iterations. Exceeding it
    # produces a ``per_tool_call_limit_exceeded`` ToolResult and skips
    # the call (the loop continues with the next call). Default of 5 is
    # generous enough for normal multi-step workflows but tight enough
    # to prevent a misbehaving provider from spamming a single tool
    # (e.g. a "send_email" tool called 100 times in a row).
    max_calls_per_tool: int = 5


class PhaseStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class PhaseEvent(BaseModel):
    """One execution-trace entry produced by a single loop phase."""

    phase: str
    status: PhaseStatus
    execution_id: str
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    duration_ms: float = 0.0
    error: str | None = None
    output: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# A phase callable: async function taking (agent, ctx_dict) and returning
# arbitrary output (stored on the PhaseEvent). ctx_dict is a mutable dict
# shared across phases of a single loop run — phases read prior outputs
# from it and write their own.
PhaseFn = Callable[["Agent", dict[str, Any]], Awaitable[Any]]
EventHook = Callable[[PhaseEvent], Awaitable[None] | None]


class AgentLoop:
    """Runs the universal agent loop for a single `run()` invocation.

    The loop is created per-execution (cheap). It is given the Agent
    reference so each phase can look up registries via the runtime.
    """

    DEFAULT_TIMEOUT_SECONDS = 60.0

    def __init__(
        self,
        agent: "Agent",
        *,
        phases: tuple[str, ...] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.agent = agent
        self.phases: tuple[str, ...] = phases or CANONICAL_PHASES
        self.timeout_seconds: float = (
            timeout_seconds
            if timeout_seconds is not None
            else self.DEFAULT_TIMEOUT_SECONDS
        )
        self.trace: list[PhaseEvent] = []
        self._hooks: list[EventHook] = []

    # -- hooks -----------------------------------------------------------

    def add_hook(self, hook: EventHook) -> None:
        self._hooks.append(hook)

    def _emit(self, event: PhaseEvent) -> None:
        for hook in list(self._hooks):
            try:
                result = hook(event)
                if hasattr(result, "__await__"):
                    asyncio.ensure_future(result)  # noqa: PLC0415
            except Exception:
                # Hooks must never break the loop.
                continue

    # -- execution -------------------------------------------------------

    async def execute_phase(
        self,
        name: str,
        fn: PhaseFn,
        ctx: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> PhaseEvent:
        """Run one phase with timing, error capture, and timeout."""
        execution_id = ctx.get("execution_id", "")
        event = PhaseEvent(phase=name, status=PhaseStatus.SUCCESS, execution_id=execution_id)
        start = time.perf_counter()
        try:
            output = await asyncio.wait_for(
                fn(self.agent, ctx),
                timeout=timeout or self.timeout_seconds,
            )
            event.output = output
            ctx[name] = output
        except asyncio.TimeoutError:
            event.status = PhaseStatus.TIMEOUT
            event.error = f"phase '{name}' timed out"
        except Exception as exc:  # noqa: BLE001
            event.status = PhaseStatus.FAILURE
            event.error = f"{type(exc).__name__}: {exc}"
        finally:
            event.duration_ms = (time.perf_counter() - start) * 1000.0
            event.finished_at = utc_now()
            self.trace.append(event)
            self._emit(event)
        return event

    async def run(
        self,
        phase_fns: dict[str, PhaseFn],
        ctx: dict[str, Any],
    ) -> list[PhaseEvent]:
        """Run all phases in order. Stops early on FAILURE or BLOCKED."""
        for name in self.phases:
            fn = phase_fns.get(name)
            if fn is None:
                event = PhaseEvent(
                    phase=name,
                    status=PhaseStatus.SKIPPED,
                    execution_id=ctx.get("execution_id", ""),
                    error="no implementation registered",
                )
                event.finished_at = utc_now()
                self.trace.append(event)
                self._emit(event)
                continue
            event = await self.execute_phase(name, fn, ctx)
            if event.status in {PhaseStatus.FAILURE, PhaseStatus.BLOCKED}:
                # Stop the loop on hard failures. Timeouts are recoverable
                # if a downstream phase explicitly handles them.
                break
        return self.trace


__all__ = [
    "AgentLoop",
    "PhaseEvent",
    "PhaseStatus",
    "PhaseFn",
    "EventHook",
    "CANONICAL_PHASES",
    "COGNITIVE_INNER_PHASES",
    "COGNITIVE_PRE_PHASES",
    "COGNITIVE_POST_PHASES",
    "CognitiveLoopConfig",
]
