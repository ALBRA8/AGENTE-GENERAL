"""WorkflowGraph — DAG built from a Workflow definition.

NODE / EDGE / CONDITION / INTERRUPT / RETRY / FALLBACK / HITL primitives
are encoded as:
  - NODE     -> a WorkflowStep in the graph
  - EDGE     -> an edge in `self._edges` (src -> dst)
  - CONDITION-> `WorkflowStep.branches` list (evaluated after success)
  - INTERRUPT-> a HITL step (type=HITL) that pauses the executor
  - RETRY    -> `WorkflowStep.retry_policy` (consumed by executor)
  - FALLBACK -> a successor step whose name starts with `fallback:`
  - HITL     -> `WorkflowStep.hitl_required` flag + interrupt step

The graph detects cycles at build time and raises WorkflowCycleError.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from albra_seed.core.base import Workflow
from albra_seed.workflows.step import WorkflowStep, WorkflowStepType


class WorkflowGraphError(Exception):
    pass


class WorkflowCycleError(WorkflowGraphError):
    """Raised when a cycle is detected in the workflow DAG."""


class WorkflowValidationError(WorkflowGraphError):
    """Raised when a step references an unknown dependency or branch target."""


class WorkflowGraph:
    """DAG of WorkflowStep nodes connected by edges."""

    def __init__(self, *, name: str = "anonymous", version: str = "1.0.0") -> None:
        self.name = name
        self.version = version
        self._steps: dict[str, WorkflowStep] = {}
        self._edges: dict[str, list[str]] = defaultdict(list)  # src -> [dsts]
        self._entry_steps: list[str] = []

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def add_step(self, step: WorkflowStep) -> "WorkflowGraph":
        if step.id in self._steps:
            raise WorkflowValidationError(f"duplicate step id: {step.id}")
        self._steps[step.id] = step
        return self

    def add_edge(self, src: str, dst: str) -> "WorkflowGraph":
        self._require_step(src)
        self._require_step(dst)
        if dst not in self._edges[src]:
            self._edges[src].append(dst)
        return self

    def add_edges(self, *pairs: tuple[str, str]) -> "WorkflowGraph":
        for src, dst in pairs:
            self.add_edge(src, dst)
        return self

    # ------------------------------------------------------------------
    # Build from a Workflow definition
    # ------------------------------------------------------------------
    @classmethod
    def from_workflow(cls, wf: Workflow) -> "WorkflowGraph":
        graph = cls(name=wf.name, version=wf.version)
        for raw_step in wf.steps:
            data = dict(raw_step)
            sid = data.get("id") or data.get("name")
            if not sid:
                raise WorkflowValidationError("step has no id/name")
            data.setdefault("id", sid)
            if "name" not in data:
                data["name"] = sid
            graph.add_step(WorkflowStep.model_validate(data))
        # Edges from explicit `edges` list
        for raw_edge in wf.edges:
            src = raw_edge.get("src") or raw_edge.get("from")
            dst = raw_edge.get("dst") or raw_edge.get("to")
            if not src or not dst:
                raise WorkflowValidationError(f"invalid edge: {raw_edge}")
            graph.add_edge(src, dst)
        # Implicit edges from `dependencies`
        for step in list(graph._steps.values()):
            for dep in step.dependencies:
                if dep in graph._steps:
                    graph.add_edge(dep, step.id)
        graph.validate()
        return graph

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def step(self, step_id: str) -> WorkflowStep:
        return self._require_step(step_id)

    def steps(self) -> list[WorkflowStep]:
        return list(self._steps.values())

    def successors(self, step_id: str) -> list[str]:
        return list(self._edges.get(step_id, []))

    def entry_steps(self) -> list[str]:
        """Steps that are never a destination of any edge."""
        targets: set[str] = set()
        for dsts in self._edges.values():
            targets.update(dsts)
        return [sid for sid in self._steps if sid not in targets]

    def topological_order(self) -> list[str]:
        in_degree = {sid: 0 for sid in self._steps}
        for src, dsts in self._edges.items():
            for dst in dsts:
                in_degree[dst] = in_degree.get(dst, 0) + 1
        q: deque[str] = deque([sid for sid, d in in_degree.items() if d == 0])
        order: list[str] = []
        while q:
            sid = q.popleft()
            order.append(sid)
            for dst in self._edges.get(sid, []):
                in_degree[dst] -= 1
                if in_degree[dst] == 0:
                    q.append(dst)
        if len(order) != len(self._steps):
            raise WorkflowCycleError("cycle detected in workflow graph")
        return order

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self) -> None:
        if not self._steps:
            return
        # Check dependencies and branch targets reference existing steps.
        for step in self._steps.values():
            for dep in step.dependencies:
                if dep not in self._steps:
                    raise WorkflowValidationError(
                        f"step '{step.id}' depends on unknown step '{dep}'"
                    )
            for branch in step.branches:
                tgt = branch.get("target") or branch.get("to")
                if tgt and tgt not in self._steps:
                    raise WorkflowValidationError(
                        f"step '{step.id}' branches to unknown step '{tgt}'"
                    )
        # Detect cycles.
        self.topological_order()

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------
    def has_cycle(self) -> bool:
        try:
            self.topological_order()
            return False
        except WorkflowCycleError:
            return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "steps": [s.model_dump(mode="json") for s in self._steps.values()],
            "edges": [
                {"src": src, "dst": dst}
                for src, dsts in self._edges.items()
                for dst in dsts
            ],
        }

    # ------------------------------------------------------------------
    def _require_step(self, step_id: str) -> WorkflowStep:
        step = self._steps.get(step_id)
        if step is None:
            raise WorkflowValidationError(f"unknown step: {step_id}")
        return step


__all__ = [
    "WorkflowGraph",
    "WorkflowGraphError",
    "WorkflowCycleError",
    "WorkflowValidationError",
]
