# Agent Platform Expansion

## Overview

This expansion adds four modular subsystems to ALBRA SEED:

1. **Orchestration** — task graphs with parallel/sequential execution
2. **Toolkit** — uniform tool system with permissions and invocation logging
3. **Planning** — typed objectives, subtasks, budgets, and validators
4. **Observability** — leverages the existing `observability/tracer.py` and `logger.py`

All modules are **new** — they do not modify existing code. They integrate
with the existing `Runtime`, `ToolRegistry`, and `LLMProvider` interfaces.

## Architecture

```
src/albra_seed/
├── orchestration/
│   ├── __init__.py          # Public API
│   └── engine.py            # TaskGraph, Orchestrator, RetryPolicy
├── toolkit/
│   ├── __init__.py          # Public API
│   └── registry.py          # ToolRegistryV2, ToolDefinition, permissions
├── planning/
│   ├── __init__.py          # Public API
│   └── engine.py            # Plan, Subtask, PlanBudget, PlanExecutor
```

## Integration with existing Runtime

The new modules are designed to be composed with the existing runtime:

```python
from albra_seed.orchestration import Orchestrator, Task, TaskGraph
from albra_seed.toolkit import ToolRegistryV2, ToolDefinition, ToolCategory
from albra_seed.planning import Plan, PlanExecutor, Subtask, PlanBudget

# Create a tool registry with built-in safe tools
from albra_seed.toolkit.registry import _create_default_registry
tool_reg = _create_default_registry()

# Define an orchestration graph
graph = TaskGraph()
graph.add_task(Task(name="fetch", task_id="fetch"))
graph.add_task(Task(name="process", task_id="process", dependencies=["fetch"]))

# Execute
orch = Orchestrator(max_concurrency=4)
result = await orch.execute(graph, {"fetch": fetch_fn, "process": process_fn})
```

## Modules

### Orchestration (`albra_seed.orchestration`)

- `TaskGraph`: DAG with cycle detection (DFS) and topological sort (Kahn's algorithm).
- `Orchestrator`: executes waves in parallel, respects `max_concurrency`.
- `Task`: unit of work with `dependencies`, `timeout`, `retry` policy.
- `RetryPolicy`: `max_attempts`, exponential `backoff`.
- `TaskState`: pending/running/completed/failed/cancelled/skipped.
- Required vs optional dependencies (required failure cancels dependents;
  optional failure skips dependents).
- Per-task duration tracking.

### Toolkit (`albra_seed.toolkit`)

- `ToolDefinition`: declarative metadata (name, description, version,
  category, input/output schemas, timeout, capabilities).
- `ToolPermission`: per-tool RBAC — `category` (read_only/side_effect/canary),
  `requires_approval`, `allowed_autonomy_levels`, `denied_principals`.
- `ToolRegistryV2`: register, list, execute with permission check + timeout.
- `ToolInvocation`: invocation log (duration, success, redacted output
  summary — NO secrets).
- Built-in safe tools: `echo`, `calculator`, `timestamp`.

### Planning (`albra_seed.planning`)

- `Plan`: objective + subtasks + budget.
- `Subtask`: typed unit with dependencies, completion criteria, validator.
- `PlanBudget`: `max_steps`, `max_duration_s`, `max_tool_calls` — anti-infinite-loop.
- `PlanExecutor`: executes subtasks in dependency order, validates outputs,
  enforces budget, detects deadlocks.
- Progress tracking: `progress_pct()`, `all_completed()`, `any_failed()`.

### Observability

The expansion leverages the existing `albra_seed.observability` module:
- `StructuredLogger` with built-in secret redaction (`logger.py`).
- `Tracer` with span-based tracing (`tracer.py`).
- All new modules emit structured events via `structlog`.
- OpenTelemetry integration is optional (lazy import — never required).

## Installation

No new dependencies are required for the core expansion. The modules
use only `pydantic`, `structlog`, and `asyncio` — all already in the
project's dependencies.

```bash
pip install -e '.[dev,a2a,api]'
```

## Runnable examples

These examples use only local deterministic behavior; they do not call external
providers or require credentials:

```bash
python docs/agent-platform-expansion/examples/workflow_recovery.py
python docs/agent-platform-expansion/examples/tool_context_trace.py
```

The first example retries a transient task failure and passes its verified
result to a dependent task. The second uses a registered tool, a validated
input/output contract, `RunContext`, and the in-process `Tracer`.

## Safety contracts

- The toolkit enforces a deliberately small JSON Schema subset (types,
  required properties, enums, nested object properties, array items, and
  `additionalProperties: false`). It is not a full JSON Schema implementation.
- A tool declared as `side_effect` requires `has_approval=True` at invocation
  time in addition to its configured permission policy. This boolean is a
  caller-supplied signal, not an approval workflow or identity provider; do
  not expose it directly to untrusted clients.
- Planning rejects unknown dependency IDs, duplicate IDs, and dependency cycles
  before executing any subtask.

## Running tests

```bash
# Orchestration tests (21)
python -m pytest tests/orchestration/ -v

# Toolkit tests (24)
python -m pytest tests/toolkit/ -v

# Planning tests (18)
python -m pytest tests/planning/ -v

# Full regression suite (750 pass)
python -m pytest tests/ -q --ignore=tests/llm_real
```

## Limitations

1. The orchestration engine does NOT replace `Agent.run()` — it composes
   multiple async callables into a verifiable graph.
2. The toolkit's `ToolRegistryV2` is separate from the existing
   `ToolRegistry` in `albra_seed.tools.registry`. It extends, not replaces.
3. The planning engine does NOT call LLMs directly — callers provide
   callables. The plan executor enforces budgets and validates outputs.
4. No new OpenTelemetry exporters are configured — the existing
   `observability/tracer.py` is used as-is.
5. The dashboard provider settings (`settings_store.py`, `provider_settings.py`,
   `dashboard/src/app/page.tsx`) were NOT modified (reserved for the other
   engineer).

## Design decisions

- **Pydantic models** for all data structures — consistent with the
  existing codebase.
- **asyncio** for all execution — no threading.
- **Wave-based parallel execution** — tasks whose dependencies are all
  completed run in parallel within each wave.
- **Separation of concerns** — orchestration, tools, and planning are
  independent modules that can be used standalone or composed.
- **No new dependencies** — the expansion uses only what's already
  in `pyproject.toml`.
