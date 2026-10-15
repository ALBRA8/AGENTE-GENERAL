"""Runnable offline example: retry a transient failure before a dependent task.

Run from the repository root after installing the project:
    python docs/agent-platform-expansion/examples/workflow_recovery.py
"""
from __future__ import annotations

import asyncio
from typing import Any

from albra_seed.orchestration import Orchestrator, RetryPolicy, Task, TaskGraph


async def main() -> None:
    graph = TaskGraph()
    graph.add_task(
        Task(
            name="fetch",
            task_id="fetch",
            retry=RetryPolicy(max_attempts=2, backoff_base=0.01, backoff_cap=0.05),
        )
    )
    graph.add_task(Task(name="transform", task_id="transform", dependencies=["fetch"]))
    attempts = 0

    async def fetch(_: dict[str, Any]) -> dict[str, str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("simulated transient failure")
        return {"value": "verified input"}

    async def transform(dependencies: dict[str, Any]) -> str:
        return dependencies["fetch"]["value"].upper()

    result = await Orchestrator(max_concurrency=2).execute(
        graph, {"fetch": fetch, "transform": transform}
    )
    print(
        {
            "success": result.success,
            "fetch_attempts": result.results["fetch"].attempts,
            "transform_state": result.results["transform"].state.value,
            "output": result.results["transform"].output,
        }
    )
    if not result.success:
        raise SystemExit("workflow failed; inspect task results")


if __name__ == "__main__":
    asyncio.run(main())
