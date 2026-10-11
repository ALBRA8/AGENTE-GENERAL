"""WorkflowEngine — high-level façade over WorkflowGraph + WorkflowExecutor.

Supports:
  - steps, dependencies, branching, conditions
  - retries, timeout, cancellation, failure, recovery (fallback)
  - HITL interrupt + resume
  - evidence collection (per-step evidence_ids on the trace)
  - execution trace (ExecutionTrace integration)
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from albra_seed.core.base import Workflow
from albra_seed.core.types import ExecutionStatus, RunContext, new_id, utc_now
from albra_seed.execution.trace import ExecutionTrace
from albra_seed.workflows.executor import (
    StepHandler,
    WorkflowExecutionFailed,
    WorkflowExecutor,
)
from albra_seed.workflows.graph import WorkflowGraph
from albra_seed.workflows.state import WorkflowRunStatus, WorkflowState


class WorkflowEngine:
    """High-level workflow engine: register definitions + handlers, run."""

    def __init__(
        self,
        *,
        executor: WorkflowExecutor | None = None,
        graphs: dict[str, WorkflowGraph] | None = None,
        handlers: dict[str, StepHandler] | None = None,
    ) -> None:
        self.executor = executor or WorkflowExecutor()
        self._graphs: dict[str, WorkflowGraph] = dict(graphs or {})
        if handlers:
            for sid, h in handlers.items():
                self.executor.register(sid, h)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register_workflow(self, wf: Workflow) -> WorkflowGraph:
        graph = WorkflowGraph.from_workflow(wf)
        self._graphs[graph.name] = graph
        return graph

    def register_graph(self, graph: WorkflowGraph) -> None:
        self._graphs[graph.name] = graph

    def register_handler(self, step_id: str, handler: StepHandler) -> None:
        self.executor.register(step_id, handler)

    def get_graph(self, name: str) -> WorkflowGraph | None:
        return self._graphs.get(name)

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------
    async def run(
        self,
        name: str,
        *,
        context: RunContext | None = None,
        state: WorkflowState | None = None,
    ) -> tuple[WorkflowState, ExecutionTrace]:
        graph = self._graphs.get(name)
        if graph is None:
            raise KeyError(f"workflow '{name}' is not registered")
        # Stash graph reference so executor's fallback lookup works.
        if state is None:
            state = WorkflowState(workflow_name=name, run_id=new_id("wfrun"))
        state.metadata["__graph__"] = graph

        trace = ExecutionTrace(
            execution_id=context.execution_id if context else new_id("exec"),
            correlation_id=context.correlation_id if context else new_id("corr"),
            agent_id="workflow-engine",
            task=name,
            status=ExecutionStatus.RUNNING,
        )
        # Walk topological order so we can record per-step trace entries.
        order = graph.topological_order()
        for step_id in order:
            step = graph.step(step_id)
            tstep = trace.begin_step(step_id, inputs=dict(step.inputs))
            tstep.add_decision("scheduled", type=step.type.value)

        try:
            state = await self.executor.execute(graph, context=context, state=state)
        except WorkflowExecutionFailed as exc:
            state.fail(str(exc))

        # Update trace from state.
        for step_id in order:
            tstep = trace.step(step_id)
            if tstep is None:
                continue
            outputs = state.step_outputs.get(step_id)
            if outputs is not None:
                tstep.finish(outputs=outputs, status="success")
            elif step_id in state.visited:
                tstep.finish(outputs={}, status="success")
            else:
                tstep.finish(
                    outputs={"skipped": True},
                    status="skipped",
                )

        if state.status == WorkflowRunStatus.PAUSED:
            trace.finish(status=ExecutionStatus.AWAITING_APPROVAL)
        elif state.status == WorkflowRunStatus.SUCCEEDED:
            trace.finish(status=ExecutionStatus.SUCCEEDED)
        elif state.status == WorkflowRunStatus.CANCELLED:
            trace.finish(status=ExecutionStatus.CANCELLED)
        else:
            trace.finish(status=ExecutionStatus.FAILED)
        for err in state.errors:
            trace.errors.append(err)
        return state, trace

    async def resume(
        self,
        name: str,
        state: WorkflowState,
        *,
        human_input: dict[str, Any] | None = None,
        context: RunContext | None = None,
    ) -> tuple[WorkflowState, ExecutionTrace]:
        graph = self._graphs.get(name)
        if graph is None:
            raise KeyError(f"workflow '{name}' is not registered")
        state.metadata["__graph__"] = graph
        state = await self.executor.resume(graph, state, human_input=human_input, context=context)
        # Re-run with the updated state to record completion + trace.
        return await self.run(name, context=context, state=state)


__all__ = ["WorkflowEngine"]
