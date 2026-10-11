"""WorkflowExecutor — runs a WorkflowGraph, supporting interrupt + resume.

The executor walks the graph in topological order. For each step it:

  1. Looks up the registered handler for the step type / id.
  2. Applies retry + timeout (via the resilience subpackage).
  3. If `hitl_required` is True (or type == HITL), the executor
     *pauses* execution, marks the state `paused=True` and
     `hitl_pending=<step_id>`, and returns the partial state to the
     caller. The caller resumes execution later by calling
     `resume(state, human_input=...)`.
  4. If a step fails after retries and has a `fallback:` successor,
     execution falls through to that successor; otherwise the workflow
     fails.
  5. Branches (CONDITION primitive) are evaluated after the step
     succeeds; the first matching branch target becomes the next step.

Handlers are async callables: `async def handler(step, state, context) -> dict`.
The SEED ships no domain handlers — only a generic echo handler used in
tests.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from albra_seed.core.types import ExecutionStatus, RunContext, new_id, utc_now
from albra_seed.execution.trace import ExecutionTrace
from albra_seed.execution.step import TraceStep
from albra_seed.workflows.graph import WorkflowGraph
from albra_seed.workflows.state import WorkflowRunStatus, WorkflowState
from albra_seed.workflows.step import WorkflowStep, WorkflowStepType


StepHandler = Callable[[WorkflowStep, WorkflowState, RunContext | None], Awaitable[dict[str, Any]]]


class WorkflowExecutionInterrupted(Exception):
    """Raised when execution pauses for HITL — handled by the executor."""

    def __init__(self, step_id: str, state: WorkflowState) -> None:
        self.step_id = step_id
        self.state = state
        super().__init__(f"workflow paused at HITL step '{step_id}'")


class WorkflowExecutionFailed(Exception):
    pass


class WorkflowExecutor:
    """Executes a WorkflowGraph, supporting interrupt + resume (HITL)."""

    def __init__(
        self,
        *,
        handlers: dict[str, StepHandler] | None = None,
        default_handler: StepHandler | None = None,
        default_timeout: float = 30.0,
    ) -> None:
        self._handlers: dict[str, StepHandler] = dict(handlers or {})
        self._default_handler = default_handler
        self.default_timeout = default_timeout

    def register(self, step_id: str, handler: StepHandler) -> None:
        self._handlers[step_id] = handler

    # ------------------------------------------------------------------
    # Execute (full run, or partial — pauses on HITL)
    # ------------------------------------------------------------------
    async def execute(
        self,
        graph: WorkflowGraph,
        *,
        context: RunContext | None = None,
        state: WorkflowState | None = None,
    ) -> WorkflowState:
        if state is None:
            state = WorkflowState(
                workflow_name=graph.name,
                run_id=new_id("wfrun"),
            )
        if context is None:
            context = RunContext(agent_id="workflow-executor", task=graph.name)

        if state.status == WorkflowRunStatus.PAUSED:
            # Resume: clear paused flag; loop will re-enter current step.
            state.paused = False
            state.status = WorkflowRunStatus.AWAITING_RESUME
        elif state.status == WorkflowRunStatus.AWAITING_RESUME:
            pass
        elif state.is_terminal:
            return state

        # Make the graph available to the fallback lookup during step runs.
        state.metadata["__graph__"] = graph

        order = graph.topological_order()
        # If resuming, skip already-visited nodes.
        remaining = [s for s in order if s not in state.visited]

        state.status = WorkflowRunStatus.RUNNING
        try:
            for step_id in remaining:
                step = graph.step(step_id)
                # HITL interrupt: pause + return state to caller.
                if step.hitl_required or step.type == WorkflowStepType.HITL:
                    if step.id not in state.hitl_inputs:
                        state.pause_for_hitl(step.id)
                        return state
                    # HITL already satisfied by resume -> treat as completed
                    # with the human input as outputs.
                    state.complete(step.id, outputs={"human_input": state.hitl_inputs[step.id]})
                    continue

                state.enter(step.id)
                outputs = await self._run_step(step, state, context)
                state.complete(step.id, outputs=outputs)

                # Branching: first matching condition wins.
                next_target = self._eval_branches(step, state, outputs)
                if next_target is not None:
                    # Skip the remaining linear steps until we reach target.
                    # (Topological order guarantees target appears later.)
                    try:
                        idx = remaining.index(next_target)
                        remaining = remaining[idx:]
                    except ValueError:
                        # target may already be visited (loop protection)
                        pass
            state.succeed()
        except WorkflowExecutionFailed as exc:
            state.fail(str(exc))
        except asyncio.CancelledError:
            state.cancel()
            raise
        return state

    # ------------------------------------------------------------------
    # Resume after HITL pause
    # ------------------------------------------------------------------
    async def resume(
        self,
        graph: WorkflowGraph,
        state: WorkflowState,
        *,
        human_input: dict[str, Any] | None = None,
        context: RunContext | None = None,
    ) -> WorkflowState:
        state.resume(human_input=human_input)
        return await self.execute(graph, context=context, state=state)

    # ------------------------------------------------------------------
    # Step execution (retry + timeout)
    # ------------------------------------------------------------------
    async def _run_step(
        self,
        step: WorkflowStep,
        state: WorkflowState,
        context: RunContext,
    ) -> dict[str, Any]:
        handler = self._handlers.get(step.id) or self._default_handler
        if handler is None:
            raise WorkflowExecutionFailed(
                f"no handler registered for step '{step.id}'"
            )

        attempts = step.retry_policy.max_retries + 1
        backoff = step.retry_policy.base_delay_seconds
        max_backoff = step.retry_policy.max_delay_seconds
        last_error: str | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = await asyncio.wait_for(
                    handler(step, state, context),
                    timeout=step.timeout_seconds or self.default_timeout,
                )
                return result or {}
            except asyncio.TimeoutError:
                last_error = f"step '{step.id}' timed out after {step.timeout_seconds}s"
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                await asyncio.sleep(min(backoff * (2 ** (attempt - 1)), max_backoff))
        # Retries exhausted — try fallback successor.
        fallback = self._find_fallback(step, state)
        if fallback is not None:
            state.warnings.append(
                f"step '{step.id}' failed ({last_error}); falling back to '{fallback.id}'"
            )
            return {"_fallback_from": step.id, "_fallback_error": last_error}
        raise WorkflowExecutionFailed(
            f"step '{step.id}' failed after {attempts} attempt(s): {last_error}"
        )

    # ------------------------------------------------------------------
    # Branching + fallback
    # ------------------------------------------------------------------
    def _eval_branches(
        self,
        step: WorkflowStep,
        state: WorkflowState,
        outputs: dict[str, Any],
    ) -> str | None:
        for branch in step.branches:
            cond = branch.get("condition")
            target = branch.get("target") or branch.get("to")
            if target is None:
                continue
            if cond is None:
                return target
            # Simple condition forms:
            #   {"status": "ok"} -> outputs["status"] == "ok"
            #   {"output_eq": {"k": v}} -> outputs["k"] == v
            if isinstance(cond, dict):
                if "output_eq" in cond and isinstance(cond["output_eq"], dict):
                    if all(outputs.get(k) == v for k, v in cond["output_eq"].items()):
                        return target
                else:
                    if all(outputs.get(k) == v for k, v in cond.items()):
                        return target
            # callable form is not supported in serialized workflows.
        return None

    def _find_fallback(self, step: WorkflowStep, state: WorkflowState) -> WorkflowStep | None:
        # Fallback: a successor whose name starts with `fallback:` OR
        # whose id begins with `fallback_`.
        graph = state.metadata.get("__graph__")
        if graph is None:
            return None
        for succ_id in graph.successors(step.id):
            succ = graph.step(succ_id)
            if succ.name.startswith("fallback:") or succ.id.startswith("fallback_"):
                return succ
        return None


__all__ = [
    "WorkflowExecutor",
    "StepHandler",
    "WorkflowExecutionInterrupted",
    "WorkflowExecutionFailed",
]
