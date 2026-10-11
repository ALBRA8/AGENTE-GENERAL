"""ExecutionRunner — orchestrates a single execution end-to-end.

Given a task description and a RunContext, the runner walks the canonical
agent phases (RECEIVE → UNDERSTAND → REASON → PLAN → CHECK_PERMISSIONS
→ EXECUTE → OBSERVE → VALIDATE → RECORD → RESPOND), records every phase
as a TraceStep on an ExecutionTrace, captures tool/provider call sites,
errors, warnings, and evidence identifiers, and returns the finished
trace + the assembled AgentResponse.

The runner is universal: it has no domain knowledge. It delegates the
actual phase work to callables (a `phase_handlers` dict mapping phase
name -> async callable). Specialization layers supply their own handlers;
the seed ships a minimal default that uses an EchoTool-style echo.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable

from albra_seed.core.types import (
    AgentResponse,
    ExecutionStatus,
    Role,
    RunContext,
    ToolResult,
    new_id,
    utc_now,
)
from albra_seed.execution.trace import ExecutionTrace
from albra_seed.execution.step import TraceStep

PhaseHandler = Callable[[RunContext, TraceStep], Awaitable[dict[str, Any]]]

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


class ExecutionRunner:
    """Runs a single task through the canonical agent phases.

    Each phase handler receives (RunContext, TraceStep) and returns a dict
    that is recorded as the step's outputs. Handlers may raise; the runner
    captures the exception into the step's errors list and marks the step
    status as `failure`.
    """

    def __init__(
        self,
        *,
        phase_handlers: dict[str, PhaseHandler] | None = None,
        phases: tuple[str, ...] | None = None,
        default_timeout_seconds: float = 30.0,
    ) -> None:
        self.phase_handlers: dict[str, PhaseHandler] = dict(phase_handlers or {})
        self.phases: tuple[str, ...] = phases or CANONICAL_PHASES
        self.default_timeout_seconds = default_timeout_seconds

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------
    def register_handler(self, phase: str, handler: PhaseHandler) -> None:
        self.phase_handlers[phase] = handler

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    async def run(
        self,
        task: str,
        context: RunContext | None = None,
        *,
        agent_id: str = "seed-agent",
    ) -> tuple[ExecutionTrace, AgentResponse]:
        if context is None:
            context = RunContext(agent_id=agent_id, task=task)
        context.task = task
        context.status = ExecutionStatus.RUNNING
        context.started_at = utc_now()

        trace = ExecutionTrace(
            execution_id=context.execution_id,
            correlation_id=context.correlation_id,
            parent_execution_id=context.parent_execution_id,
            agent_id=agent_id,
            task=task,
            status=ExecutionStatus.RUNNING,
        )

        aborted = False
        for phase in self.phases:
            handler = self.phase_handlers.get(phase)
            step = trace.begin_step(phase, inputs={"task": task})
            if handler is None:
                step.finish(outputs={"skipped": True}, status="skipped")
                continue
            try:
                outputs = await asyncio.wait_for(
                    handler(context, step), timeout=self.default_timeout_seconds
                )
                step.finish(outputs=outputs or {}, status="success")
            except asyncio.TimeoutError:
                step.finish(
                    outputs={"timed_out": True},
                    status="timeout",
                    errors=[f"phase '{phase}' timed out"],
                )
                trace.errors.append(f"phase '{phase}' timed out")
                aborted = True
                break
            except Exception as exc:  # noqa: BLE001
                step.finish(
                    outputs={"error": str(exc)},
                    status="failure",
                    errors=[f"{type(exc).__name__}: {exc}"],
                )
                trace.errors.append(f"phase '{phase}' failed: {exc}")
                aborted = True
                break

        final_status = ExecutionStatus.FAILED if aborted else ExecutionStatus.SUCCEEDED
        context.status = final_status
        context.finished_at = utc_now()
        context.errors.extend(trace.errors)
        context.warnings.extend(trace.warnings)
        context.outputs["trace"] = trace.to_dict()

        trace.finish(status=final_status)

        response = self._build_response(context, trace)
        return trace, response

    # ------------------------------------------------------------------
    # Response assembly
    # ------------------------------------------------------------------
    def _build_response(self, context: RunContext, trace: ExecutionTrace) -> AgentResponse:
        respond_step = trace.step("RESPOND")
        content = ""
        if respond_step is not None:
            content = str(respond_step.outputs.get("content", ""))
        if not content:
            content = context.task
        return AgentResponse(
            content=content,
            role=Role.ASSISTANT,
            execution_id=context.execution_id,
            correlation_id=context.correlation_id,
            confidence=float(respond_step.outputs.get("confidence", 0.0)) if respond_step else 0.0,
            evidence_ids=list(trace.evidence_ids),
            warnings=list(trace.warnings),
            errors=list(trace.errors),
            metadata={
                "agent_id": trace.agent_id,
                "tools_used": list(context.tools_used),
                "providers_used": list(context.providers_used),
                "trace_steps": len(trace.steps),
            },
        )


__all__ = ["ExecutionRunner", "PhaseHandler", "CANONICAL_PHASES"]
