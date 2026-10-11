# ALBRA AGENT SEED V1.0 — Examples Index

> 15 runnable example scripts that demonstrate every major subsystem in
> CORE_ONLY mode. **NO domain knowledge** — each example uses only the
> universal infra shipped by the seed.

Each example is 30–80 lines, well-commented, and runnable with:

```bash
python examples/<NN>_<name>.py
```

from the project root. No external credentials are required (the Mock
provider is the always-available fallback).

---

## Quick Reference

| #  | File                                  | Subsystem                  | Description                                                                  |
| -- | ------------------------------------- | -------------------------- | ---------------------------------------------------------------------------- |
| 1  | [`01_minimal_agent.py`](../examples/01_minimal_agent.py)             | runtime / agent loop       | Minimal CORE_ONLY agent run.                                                 |
| 2  | [`02_custom_tool.py`](../examples/02_custom_tool.py)                | tools                      | Define + register a custom tool, run agent that uses it.                     |
| 3  | [`03_memory_provenance.py`](../examples/03_memory_provenance.py)    | memory (MemoryDV)          | Write memory with provenance, recall it; verify isolation.                  |
| 4  | [`04_firewall_block.py`](../examples/04_firewall_block.py)          | firewall                   | Try an action blocked by firewall; verify default-deny.                       |
| 5  | [`05_hitl_approval.py`](../examples/05_hitl_approval.py)           | approvals (HITL)           | High-risk action requiring approval; approve it; verify evidence chain.      |
| 6  | [`06_workflow_with_hitl.py`](../examples/06_workflow_with_hitl.py) | workflows                   | Workflow that pauses for HITL, resumes.                                      |
| 7  | [`07_provider_fallback.py`](../examples/07_provider_fallback.py)   | providers                   | Register a fake provider that fails; fallback to Mock.                       |
| 8  | [`08_kill_switch.py`](../examples/08_kill_switch.py)                | firewall / kill switch      | Activate the kill switch; verify all actions blocked; verify cooldown.      |
| 9  | [`09_audit_tamper_detection.py`](../examples/09_audit_tamper_detection.py) | audit (tamper-evident)     | Append audit entries, mutate one, verify detection.                         |
| 10 | [`10_doctor_diagnose.py`](../examples/10_doctor_diagnose.py)        | doctor                      | Run the Doctor; print the per-component diagnostic report.                   |
| 11 | [`11_handoff_between_agents.py`](../examples/11_handoff_between_agents.py) | handoff                     | Agent A hands off to Agent B; verify response + evidence chain.              |
| 12 | [`12_mcp_server_client.py`](../examples/12_mcp_server_client.py)    | mcp                         | Set up an in-memory MCP server + client; discover + invoke a capability.     |
| 13 | [`13_workflow_engine_simple.py`](../examples/13_workflow_engine_simple.py) | workflows                   | A simple 3-step linear workflow.                                              |
| 14 | [`14_api_server.py`](../examples/14_api_server.py)                   | api                         | Start the REST API server (requires fastapi + uvicorn).                       |
| 15 | [`15_cli_usage.py`](../examples/15_cli_usage.py)                    | cli                          | Programmatic CLI usage (version, health, doctor, config in JSON).            |

---

## Running All Examples

```bash
for f in examples/*.py; do
    echo "===== $f ====="
    python "$f"
done
```

Or one at a time:

```bash
python examples/01_minimal_agent.py
python examples/02_custom_tool.py
# ...
```

---

## Example Dependencies

| Example               | Requires                                                            |
| --------------------- | ------------------------------------------------------------------- |
| 01–13, 15             | CORE_ONLY (pydantic, structlog, dotenv). No external deps.         |
| 14 (API server)       | `pip install fastapi uvicorn httpx` (clean-room SKIPs gracefully).   |

---

## What Each Example Demonstrates

### 01 — Minimal Agent
A 60-line end-to-end agent run with Mock provider. Shows `Runtime.startup()`,
`ProviderRouter` wiring, `Agent.run()` returning an `AgentResponse`.

### 02 — Custom Tool
Subclass `Tool`, define a `ToolContract` (input/output JSON schema, risk
level, permissions), register it with both `ToolRegistry` and the Runtime,
invoke it directly + via dry-run path.

### 03 — Memory Provenance
Write episodic memory (no evidence) and factual memory (with evidence_ids +
qualifying source_type). Recall within an isolation bucket; verify isolation
across `(agent_id, domain, user_id, context_id)`.

### 04 — Firewall Block
`ActionFirewall` with NO rules → DEFAULT-DENY. Add an ALLOW rule for
`tool.echo.execute`; verify ALLOW for that action and DENY for everything
else (default-deny is per-action).

### 05 — HITL Approval
`ApprovalEngine.request()` → PENDING; `approve()` → APPROVED (terminal).
Verify the tamper-evident evidence chain is intact; verify re-approve fails
(terminal state).

### 06 — Workflow with HITL
Build a 3-step workflow (gather → HITL review → finalize). The engine pauses
at the HITL step; resume with `human_input` and the engine completes.

### 07 — Provider Fallback
Build a `FailingProvider` that raises on every `complete()` call.
`ProviderFallback` transparently transitions to `MockProvider`; the
transitions log records the failure.

### 08 — Kill Switch
Build a firewall with an explicit ALLOW rule. Activate the kill switch; the
SAME action becomes DENY (kill switch overrides ALLOW). Deactivate; verify a
30-second post-deactivation cooldown is in effect.

### 09 — Audit Tamper Detection
Append 5 entries to a `TamperEvidentAuditLog`; verify the chain is intact.
Mutate one entry's `action` field; verify `verify_chain()` returns
`valid=False` and `detect_tampering()` lists the broken link.

### 10 — Doctor Diagnose
Run `Doctor().diagnose()`; print overall status + per-component breakdown.
Demonstrates honest reporting: components that fail their probe are reported
as `error` / `not_verified`, NOT masked as healthy.

### 11 — Handoff Between Agents
Register Agent B's handler via `AgentRegistry.register(AgentRecord(...))`.
Agent A issues a `HandoffRequest`; the manager routes to B's handler; the
evidence chain records the outcome.

### 12 — MCP Server + Client
Build an `InMemoryMCPServer`, register a generic capability. Connect an
`InMemoryMCPClient`; discover capabilities; invoke the capability and print
the result.

### 13 — Simple Workflow Engine
A 3-step linear workflow (step_a → step_b → step_c) where each step's
output feeds the next. Demonstrates `WorkflowEngine.run()` returning a
state + trace.

### 14 — API Server
Build a CORE_ONLY runtime + FastAPI app via `create_app(runtime)`. Use
FastAPI's `TestClient` to probe `/api/v1/health`, `/api/v1/readiness`,
and `/`. Requires `fastapi` + `uvicorn` + `httpx`.

### 15 — CLI Usage
Invoke `albra_seed.main(argv)` programmatically: `--version`, `health`,
`doctor` (alias: diagnostics), `--format json config`, `capabilities`.

---

## How to Adapt Examples for Specialization

Each example is the seed's universal behaviour. To specialize:

1. Clone the repo (see [`docs/SPECIALIZATION.md`](SPECIALIZATION.md)).
2. Replace the Mock provider with your domain provider in the examples
   that wire the ProviderRouter.
3. Replace the generic tools (echo, reverse_string) with your domain tools
   (each implementing `ToolContract`).
4. Replace the generic workflow (3-step pipeline) with your domain workflow.
5. Re-run the clean-room verification: `python scripts/clean_room_verify.py`.
6. The seed MUST still pass; your specialization tests live under
   `tests/specialization/`.

---

## Common Patterns Across Examples

- **Runtime + ProviderRouter** wiring is the same everywhere: build a
  `ProviderRegistry`, register a `MockProvider`, wrap in a `ProviderRouter`,
  register it as the `provider_router` component.
- **Identity** always carries `autonomy_level` and `permissions`; both are
  consulted by the policy engine.
- **RunContext** carries `execution_id` + `correlation_id`; both propagate
  to memory, audit, tool calls, and provider calls via `contextvars`.
- **HITL** is the default for any action that requires more autonomy than
  the agent has, OR is explicitly `REQUIRE_HITL` in a policy rule.
- **Audit** is recorded in BOTH the normal `AuditLog` (structured JSONL) and
  the `TamperEvidentAuditLog` (hash-chained, separate).

See [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) for the full data flow.
