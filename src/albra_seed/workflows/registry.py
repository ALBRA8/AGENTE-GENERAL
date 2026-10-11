"""WorkflowRegistry — register, get, and list Workflow definitions.

Lightweight in-memory registry keyed by workflow name. Used by the
WorkflowEngine to look up graphs at execution time.
"""

from __future__ import annotations

from typing import Iterable

from albra_seed.core.base import Workflow
from albra_seed.workflows.graph import WorkflowGraph


class WorkflowNotFoundError(KeyError):
    pass


class WorkflowAlreadyRegisteredError(KeyError):
    pass


class WorkflowRegistry:
    """Registers Workflow definitions and (lazily) their compiled graphs."""

    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}
        self._graphs: dict[str, WorkflowGraph] = {}

    # ------------------------------------------------------------------
    # Register / unregister
    # ------------------------------------------------------------------
    def register(self, workflow: Workflow, *, compile: bool = True) -> Workflow:
        if workflow.name in self._workflows:
            raise WorkflowAlreadyRegisteredError(workflow.name)
        self._workflows[workflow.name] = workflow
        if compile:
            self._graphs[workflow.name] = WorkflowGraph.from_workflow(workflow)
        return workflow

    def unregister(self, name: str) -> bool:
        existed = self._workflows.pop(name, None) is not None
        self._graphs.pop(name, None)
        return existed

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get(self, name: str) -> Workflow:
        wf = self._workflows.get(name)
        if wf is None:
            raise WorkflowNotFoundError(name)
        return wf

    def get_graph(self, name: str) -> WorkflowGraph:
        g = self._graphs.get(name)
        if g is None:
            wf = self.get(name)
            g = WorkflowGraph.from_workflow(wf)
            self._graphs[name] = g
        return g

    def list(self) -> list[str]:
        return list(self._workflows.keys())

    def list_workflows(self) -> list[Workflow]:
        return list(self._workflows.values())

    def __len__(self) -> int:
        return len(self._workflows)

    def __contains__(self, name: object) -> bool:
        return name in self._workflows

    def __iter__(self) -> Iterable[str]:
        return iter(self._workflows)


__all__ = [
    "WorkflowRegistry",
    "WorkflowNotFoundError",
    "WorkflowAlreadyRegisteredError",
]
