#!/usr/bin/env python3
"""13_workflow_engine_simple.py — A simple 3-step workflow.

Demonstrates a basic linear workflow:
    1. Build a 3-step Workflow (step_a → step_b → step_c).
    2. Register handlers for each step.
    3. Run the workflow end-to-end.
    4. Print the final state + step outputs.

Run:
    python examples/13_workflow_engine_simple.py
"""

from __future__ import annotations

import asyncio

from albra_seed import Workflow
from albra_seed.workflows.engine import WorkflowEngine


async def main() -> None:
    # 1. Define a 3-step linear workflow.
    wf = Workflow(
        name="simple_pipeline",
        steps=[
            {"id": "step_a", "name": "step_a", "type": "action"},
            {"id": "step_b", "name": "step_b", "type": "action"},
            {"id": "step_c", "name": "step_c", "type": "action"},
        ],
        edges=[{"src": "step_a", "dst": "step_b"},
               {"src": "step_b", "dst": "step_c"}],
    )

    # 2. Register simple handlers — each passes data through.
    async def step_a(step, state, ctx):
        return {"value": 1}
    async def step_b(step, state, ctx):
        prev = state.step_outputs.get("step_a", {})
        return {"value": prev.get("value", 0) + 1}
    async def step_c(step, state, ctx):
        prev = state.step_outputs.get("step_b", {})
        return {"value": prev.get("value", 0) * 10, "done": True}

    engine = WorkflowEngine(handlers={
        "step_a": step_a, "step_b": step_b, "step_c": step_c,
    })
    engine.register_workflow(wf)

    # 3. Run end-to-end.
    state, trace = await engine.run("simple_pipeline")
    print(f"status            : {state.status.value}")
    print(f"visited           : {state.visited}")
    print(f"step outputs:")
    for sid in ("step_a", "step_b", "step_c"):
        print(f"  {sid}: {state.step_outputs.get(sid)}")

    # 4. Trace summary.
    print(f"\ntrace:")
    print(f"  execution_id   : {trace.execution_id}")
    print(f"  trace_status   : {trace.status.value}")


if __name__ == "__main__":
    asyncio.run(main())
