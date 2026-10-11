#!/usr/bin/env python3
"""06_workflow_with_hitl.py — Workflow that pauses for HITL, resumes.

Demonstrates the WorkflowEngine's pause/resume:
    1. Build a 3-step Workflow (gather → HITL review → finalize).
    2. Register the workflow + handlers with WorkflowEngine.
    3. Run it; the engine pauses at the HITL step.
    4. Resume with human_input; the engine completes the final step.
    5. Print the final state + step outputs.

Run:
    python examples/06_workflow_with_hitl.py
"""

from __future__ import annotations

import asyncio

from albra_seed import Workflow
from albra_seed.workflows.engine import WorkflowEngine


async def main() -> None:
    # 1. Build a 3-step workflow: gather → human-review → finalize.
    wf = Workflow(
        name="hitl_demo",
        steps=[
            {"id": "gather", "name": "gather", "type": "action",
             "inputs": {"q": "demo"}},
            {"id": "review", "name": "review", "type": "hitl"},
            {"id": "finalize", "name": "finalize", "type": "action"},
        ],
        edges=[{"src": "gather", "dst": "review"},
               {"src": "review", "dst": "finalize"}],
    )

    # 2. Register handlers for the action steps (review is HITL — no handler).
    async def gather(step, state, ctx):
        return {"collected": "data-123"}
    async def finalize(step, state, ctx):
        return {"final": state.step_outputs.get("review", {})}

    engine = WorkflowEngine(handlers={"gather": gather, "finalize": finalize})
    engine.register_workflow(wf)

    # 3. Execute — pauses at the HITL step.
    state, _ = await engine.run("hitl_demo")
    print(f"after run #1     : status={state.status.value} paused={state.paused}")
    print(f"                 : hitl_pending={state.hitl_pending}")
    print(f"                 : visited={state.visited}")

    # 4. Resume with human input.
    state, _ = await engine.resume(
        "hitl_demo", state,
        human_input={"approved": True, "note": "looks ok"},
    )
    print(f"after resume     : status={state.status.value}")
    print(f"                 : visited={state.visited}")
    print(f"                 : outputs={state.step_outputs}")


if __name__ == "__main__":
    asyncio.run(main())
