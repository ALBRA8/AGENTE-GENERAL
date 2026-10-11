"""TraceReplay — reconstruct an execution from its trace.

For audit and debugging: given a serialized ExecutionTrace (or the live
object), the replayer walks each TraceStep in order, invokes the supplied
phase handlers with the recorded inputs, and produces a new trace plus
a diff against the original. Where the original step captured outputs
(e.g. tool_calls, decisions), they are reused as-is for handlers that
would otherwise be non-deterministic (LLM calls, real tool I/O).

The replayer is universal: handlers are caller-supplied. The SEED ships
no domain replay handlers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from albra_seed.core.types import ExecutionStatus, RunContext, utc_now
from albra_seed.execution.trace import ExecutionTrace
from albra_seed.execution.step import TraceStep


ReplayHandler = Callable[[RunContext, TraceStep, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class ReplayResult:
    """Outcome of a replay operation."""

    replayed: ExecutionTrace
    matched_phases: int = 0
    mismatched_phases: int = 0
    mismatches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_exact(self) -> bool:
        return self.mismatched_phases == 0


class TraceReplay:
    """Replays an ExecutionTrace using caller-supplied handlers."""

    def __init__(self, handlers: dict[str, ReplayHandler] | None = None) -> None:
        self._handlers: dict[str, ReplayHandler] = dict(handlers or {})

    def register(self, phase: str, handler: ReplayHandler) -> None:
        self._handlers[phase] = handler

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------
    async def replay(
        self,
        original: ExecutionTrace | dict[str, Any],
        *,
        context: RunContext | None = None,
        reuse_outputs: bool = True,
    ) -> ReplayResult:
        if isinstance(original, dict):
            original = ExecutionTrace.from_dict(original)

        if context is None:
            context = RunContext(
                agent_id=original.agent_id,
                task=original.task,
                execution_id=original.execution_id,
                correlation_id=original.correlation_id,
            )
        context.status = ExecutionStatus.RUNNING

        replayed = ExecutionTrace(
            execution_id=original.execution_id,
            correlation_id=original.correlation_id,
            parent_execution_id=original.parent_execution_id,
            agent_id=original.agent_id,
            task=original.task,
            status=ExecutionStatus.RUNNING,
        )

        matched = 0
        mismatched = 0
        mismatches: list[dict[str, Any]] = []

        for step in original.steps:
            handler = self._handlers.get(step.phase)
            new_step = replayed.begin_step(step.phase, inputs=dict(step.inputs))
            new_step.tool_calls = list(step.tool_calls)
            new_step.provider_calls = list(step.provider_calls)
            new_step.decisions = list(step.decisions)
            new_step.evidence_ids = list(step.evidence_ids)

            if handler is None:
                # No handler: reuse original outputs, mark as replayed-from-trace.
                new_step.finish(
                    outputs=step.outputs if reuse_outputs else {},
                    status="skipped",
                )
                matched += 1
                continue

            try:
                outputs = await handler(context, new_step, step.outputs)
                new_step.finish(outputs=outputs or {}, status="success")
                if outputs == step.outputs:
                    matched += 1
                else:
                    mismatched += 1
                    mismatches.append({
                        "phase": step.phase,
                        "original": step.outputs,
                        "replayed": outputs,
                    })
            except Exception as exc:  # noqa: BLE001
                new_step.finish(
                    outputs={"error": str(exc)},
                    status="failure",
                    errors=[f"{type(exc).__name__}: {exc}"],
                )
                mismatched += 1
                mismatches.append({
                    "phase": step.phase,
                    "error": str(exc),
                })

        replayed.finish(status=original.status)
        return ReplayResult(
            replayed=replayed,
            matched_phases=matched,
            mismatched_phases=mismatched,
            mismatches=mismatches,
        )


__all__ = ["TraceReplay", "ReplayResult", "ReplayHandler"]
