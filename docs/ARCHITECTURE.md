# ALBRA AGENT SEED V1.0 — Architecture

This document is the complete architecture map of the Seed. It explains every
component, its responsibility, its contract, and how it interacts with the rest.

---

## 1. Layered View

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          INTERFACES LAYER                                │
│         api (REST)            cli (argparse)           config             │
├─────────────────────────────────────────────────────────────────────────┤
│                       EXTENSIBILITY LAYER                                │
│       tools           skills           plugins          capabilities     │
├─────────────────────────────────────────────────────────────────────────┤
│                       INTEGRATION LAYER                                 │
│     mcp          handoff       communication       multimodal   files    │
├─────────────────────────────────────────────────────────────────────────┤
│                      OBSERVABILITY LAYER                                 │
│     observability      audit (normal + tamper-evident)                   │
│     doctor             recovery                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                        EXECUTION LAYER                                   │
│  execution (runner+trace)    workflows (graph+HITL)    jobs   scheduler  │
│  resilience (retry/timeout/circuit breaker/fallback)                    │
├─────────────────────────────────────────────────────────────────────────┤
│                         SECURITY LAYER                                   │
│  security (auth+secrets)   firewall (default deny)   policy (engine)    │
│  autonomy (L0..L5)         approvals (HITL)            kill switch        │
├─────────────────────────────────────────────────────────────────────────┤
│                    MEMORY & KNOWLEDGE LAYER                              │
│  memory (MemoryDV)        provenance         evidence (contract)         │
├─────────────────────────────────────────────────────────────────────────┤
│                         COGNITIVE LAYER                                  │
│  reasoning (engine+planner)   providers (registry+router+mock)           │
│  context (RunContext)        contracts (structured output)              │
├─────────────────────────────────────────────────────────────────────────┤
│                          CORE (immutable)                                │
│  types · identity · config · constants · base abstractions               │
├─────────────────────────────────────────────────────────────────────────┤
│                          RUNTIME                                         │
│  Runtime (lifecycle) · Agent · AgentLoop · AgentState                   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Agent Loop

The universal agent loop, implemented in `albra_seed/runtime/loop.py`:

```
RECEIVE  →  UNDERSTAND  →  REASON  →  PLAN  →  CHECK_PERMISSIONS
                                                      ↓
RESPOND  ←  RECORD     ←  VALIDATE  ←  OBSERVE  ←  EXECUTE
```

Each phase:
- Emits a `PhaseEvent` (subscribe via `runtime.add_phase_hook(fn)`)
- Has timeout (via `asyncio.wait_for`)
- Captures inputs/outputs/errors in `TraceStep`
- Appends to `ExecutionTrace` (on `RunContext.outputs["trace"]`)

The loop supports:
- **Tool calls** — dispatched via `runtime.get_component("tool_registry")`
- **Provider calls** — dispatched via `runtime.get_component("provider_router")`
- **Retries** — handled by `resilience.RetryExecutor`
- **Failures** — captured as errors in trace + audit log
- **Interruptions** — via `asyncio.CancelledError`
- **HITL** — calls `runtime.get_component("approval_engine")` when policy returns REQUIRE_HITL
- **Cancellation** — propagated cleanly
- **Timeouts** — per-phase `asyncio.wait_for`
- **Recovery** — `recovery.RecoveryManager` triggered on error class

---

## 3. Component Inventory

| Component | Subpackage | Key Class(es) | Risk | Tests |
|---|---|---|---|---|
| Core | `core/` | `Message`, `RunContext`, `Evidence`, `Provenance`, `AgentIdentity`, `SeedConfig` | LOW | test_config |
| Runtime | `runtime/` | `Runtime`, `Agent`, `AgentLoop`, `LifecycleManager`, `AgentState` | HIGH | test_runtime |
| Reasoning | `reasoning/` | `ReasoningEngine`, `DefaultReasoningEngine`, `Planner`, `PromptManager` | MEDIUM | test_reasoning |
| Providers | `providers/` | `ProviderRegistry`, `ProviderRouter`, `MockProvider`, `OpenAIProvider`, ... | MEDIUM | test_providers |
| Context | `context/` | `ContextManager`, `CorrelationTracker` | MEDIUM | test_runtime |
| Contracts | `contracts/` | `Contract`, `ContractRegistry` | LOW | — |
| Memory (MemoryDV) | `memory/` | `MemoryManager`, `SQLiteMemoryStore`, `InMemoryMemoryStore`, `MemoryProvenance`, `MemoryIsolator`, `ConsolidationPolicy` | MEDIUM | test_memory |
| Provenance | `provenance/` | `ProvenanceTracker`, `ProvenanceChain`, `SQLiteProvenanceStore` | MEDIUM | test_provenance |
| Evidence | `evidence/` | `EvidenceManager`, `EvidenceContract`, `CitationGate`, `EvidenceIntegrity` | HIGH | test_evidence |
| Security | `security/` | `Authenticator`, `NoopAuthenticator`, `Role`, `Permission`, `SecretsManager` (Env/File/Vault) | HIGH | test_security |
| Firewall | `firewall/` | `ActionFirewall`, `FirewallRule`, `KillSwitch`, `CooldownManager`, `RateLimiter` | CRITICAL | test_firewall |
| Policy | `policy/` | `PolicyEngine`, `PolicyRule`, `PolicyRuleSet`, `PolicyEvaluator` | HIGH | test_policy |
| Autonomy | `autonomy/` | `AutonomyLevel`, `AutonomyMatrix`, `AutonomyManager`, `AutonomyProfile` | HIGH | test_autonomy |
| Approvals (HITL) | `approvals/` | `ApprovalEngine`, `ApprovalRequest`, `ApprovalEvidence`, `EscalationPolicy` | HIGH | test_approvals |
| Execution | `execution/` | `ExecutionRunner`, `ExecutionTrace`, `TraceStep`, `IdempotencyManager`, `TraceReplay` | MEDIUM | test_execution |
| Workflows | `workflows/` | `WorkflowEngine`, `WorkflowGraph`, `WorkflowExecutor`, `WorkflowState`, `WorkflowRegistry` | MEDIUM | test_workflows |
| Jobs | `jobs/` | `JobManager`, `Job`, `InMemoryJobQueue`, `SQLiteJobQueue`, `JobHistory` | MEDIUM | test_jobs |
| Scheduler | `scheduler/` | `Scheduler`, `ScheduledTask`, `OneShotTrigger`, `IntervalTrigger`, `CronTrigger` | LOW | test_scheduler |
| Resilience | `resilience/` | `RetryExecutor`, `TimeoutExecutor`, `FallbackChain`, `CircuitBreaker`, `GracefulDegradation` | LOW | test_resilience |
| Observability | `observability/` | `StructuredLogger`, `MetricsRegistry`, `Tracer`, `HealthMonitor`, `ReadinessProbe` | LOW | test_observability |
| Audit | `audit/` | `AuditLog` (normal), `TamperEvidentAuditLog` (separate), `ChainVerifier` | HIGH | test_audit |
| Doctor | `doctor/` | `Doctor`, `DiagnosticReport`, `CheckRegistry`, `DiagnosticMatrix` | MEDIUM | test_doctor |
| Recovery | `recovery/` | `RecoveryManager`, `ErrorClassifier`, `RecoveryStrategy`, `RecoveryAudit` | HIGH | test_recovery |
| MCP | `mcp/` | `MCPServer`, `MCPClient`, `MCPRegistry`, `MCPValidator`, `MCPRequest/Response` | LOW | test_mcp |
| Handoff | `handoff/` | `HandoffManager`, `HandoffRequest/Response`, `AgentRegistry`, `HandoffTimeout`, `HandoffEvidence` | MEDIUM | test_handoff |
| Communication | `communication/` | `CommunicationRouter`, `ChannelAdapter`, `TextChannelAdapter`, `ChannelRegistry` | LOW | test_communication |
| Multimodal | `multimodal/` | `ModalityAdapter`, `TextAdapter`, `VoiceAdapter`, `ImageAdapter`, `DocumentAdapter` | LOW | test_multimodal |
| Files | `files/` | `FileHandler`, `FileMetadata`, `ContentExtractor`, `FileProvenance` | LOW | test_files |
| Tools | `tools/` | `ToolRegistry`, `ToolExecutor`, `DryRunExecutor`, `ContractValidator`, infra tools (Echo/Health/NoOp/Timestamp/Status) | MEDIUM | test_tools |
| Capabilities | `capabilities/` | `CapabilityRegistry`, `CapabilityResolver`, `CapabilityMatrix` | LOW | test_capabilities |
| Skills | `skills/` | `SkillRegistry`, `SkillValidator`, `SkillExecutor`, `SkillLifecycle`, `SkillContract` | LOW | test_skills |
| Plugins | `plugins/` | `PluginManager`, `PluginLoader`, `PluginValidator`, `PluginManifest` | MEDIUM | test_plugins |
| API | `api/` | `create_app`, `APIServer`, routes (health/readiness/agent/execution/diagnostics) | MEDIUM | test_api |
| CLI | `cli/` | `main`, commands (health/agent/doctor/capabilities/providers/tools/skills/memory/execution/audit/config) | LOW | test_cli |
| Config | `config/` | `ConfigManager`, `ConfigLoader`, `ConfigValidator`, `SecretsAdapter` | LOW | test_config |

---

## 4. Cross-Cutting Concerns

### 4.1 Correlation IDs

Every execution carries `execution_id` + `correlation_id` + optional `parent_execution_id`.
These propagate via `contextvars` (`CorrelationTracker`) through:
- Memory writes (provenance)
- Audit log entries
- Tool calls
- Provider calls
- Workflow steps
- MCP calls
- Handoff requests

### 4.2 Default Deny

`ActionFirewall.evaluate(action, actor, context) -> PolicyDecision`:
- Kill switch active → DENY
- Cooldown active → DENY
- Rate limit exceeded → DENY
- No matching rule → DENY
- Matching rule → ALLOW / REQUIRE_HITL / DRY_RUN

### 4.3 Evidence Discipline

`EvidenceType` ∈ {FACT, INFERENCE, ASSUMPTION, UNKNOWN}.
- FACT requires `confidence >= 0.9` AND `source_type ∈ {tool, external, verified_provider}`
- UNKNOWN NEVER auto-promotes to FACT (enforced in `EvidenceManager.promote_to_fact`)
- Every memory record has `is_fact: bool` (False by default)

### 4.4 Tamper-Evident Audit (separate from normal audit)

Two separate classes, never conflated:
- `AuditLog` — normal structured JSONL, append-only
- `TamperEvidentAuditLog` — hash-chained (`prev_hash + content_hash`)
- `ChainVerifier.verify_chain() -> VerificationResult{valid, broken_at, expected_hash, actual_hash}`

### 4.5 Autonomy vs Permission

```python
# Autonomy = what agent is ALLOWED to decide on its own
agent.autonomy_level = AutonomyLevel.L2_EXECUTE_SAFE  # can execute safe actions autonomously

# Permission = what agent is AUTHORIZED to do
agent.permissions = ["tool.echo.execute"]              # can use echo tool

# To execute an action, BOTH must be satisfied:
# 1. AutonomyLevel >= required_for_action_type_and_risk
# 2. Permission grant exists
# Otherwise: REQUIRE_HITL or DENY
```

### 4.6 Immutability of Core

The `Runtime` cannot modify `src/albra_seed/core/*` at runtime. Self-improvement
hooks exist (`albra_seed.core.hooks`) but ALL changes pass through:
1. Approval gate
2. Audit log (both normal + tamper-evident)
3. Version bump
4. Optional HITL approval

---

## 5. Data Flow

```
User input
    │
    ▼
[Communication Router]  ──text/voice/image/doc──▶ [Multimodal Adapter]
    │
    ▼
[Runtime] creates RunContext (execution_id, correlation_id)
    │
    ▼
[Agent Loop]
    │
    ├──▶ [Guardrails.check_input]
    ├──▶ [ReasoningEngine.decompose + plan]
    ├──▶ [PolicyEngine.evaluate]  ──ALLOW──▶ [ToolExecutor.execute]
    │                                  │
    │                                  REQUIRE_HITL ──▶ [ApprovalEngine.request]
    │                                  │                    │
    │                                  DRY_RUN ──▶ [DryRunExecutor]
    │                                  │
    │                                  DENY ──▶ audit + return
    │
    ├──▶ [MemoryStore.write] (with provenance + evidence)
    ├──▶ [AuditLog.append] + [TamperEvidentAuditLog.append]
    │
    ▼
[AgentResponse] returned to user
```

---

## 6. Provider Routing

```
LLM request
    │
    ▼
[ProviderRouter.route(model)]
    │
    ├─ explicit model configured?  ──▶ that provider
    │
    ├─ default provider configured? ──▶ that provider
    │
    └─ fallback chain ──▶ [MockProvider] (always available, CORE_ONLY safe)
```

All concrete providers (OpenAI, Anthropic, Groq, Ollama, ZAI) are lazy-imported.
If the SDK isn't installed OR no API key is set, the provider raises
`NotImplementedError` at init time — the router falls back to Mock.

---

## 7. Workflow Engine

`WorkflowGraph` is a DAG with:
- `NODE` — action/tool/skill/decision/handoff/wait
- `EDGE` — sequential or conditional
- `CONDITION` — branching predicate
- `INTERRUPT` — pause for HITL
- `RETRY` — retry policy per step
- `FALLBACK` — alternative path on failure

Cycle detection via Kahn's algorithm. Supports `pause()` + `resume(approval_id)`
for HITL.

---

## 8. Doctor (Diagnostics)

`Doctor.diagnose(runtime)` runs 10 built-in async checks in parallel:
- runtime_check, memory_check, providers_check, tools_check, skills_check,
  security_check, audit_check, observability_check, mcp_check, comm_check

Each check returns `CheckResult` with `status` ∈ {HEALTHY, DEGRADED, WARNING, ERROR,
BLOCKED, NOT_CONFIGURED, NOT_VERIFIED} and `implementation_status` ∈ {IMPLEMENTED,
MOCK, PLACEHOLDER, EXTERNAL_DEPENDENCY, NOT_CONFIGURED, NOT_VERIFIED}.

**The Doctor NEVER reports HEALTHY + IMPLEMENTED without actually probing the component.**

---

## 9. Recovery (Self-Healing)

`RecoveryManager.handle(error, context)`:
1. `ErrorClassifier.classify(error) -> ErrorClass` ∈ {transient, persistent, security, resource, external, unknown}
2. Look up `RecoveryStrategy` for that class
3. If class is SECURITY or UNKNOWN → REFUSE to auto-recover (require HITL)
4. Else execute strategy (retry / fallback / restart component / degrade gracefully)
5. Record EVERYTHING to both audit logs

`RecoveryManager` REFUSES to register strategies whose names match dangerous markers
(`rm_rf`, `delete_database`, `drop_table`, `wipe`, `kill_process`, ...).

---

## 10. Specialization Boundary

```
src/albra_seed/         ← IMMUTABLE (do not modify when specializing)
src/albra_specialization/   ← specialization goes here
```

A specialization adds:
- Domain `AgentIdentity` (name, version, specialization field)
- Domain knowledge (with provenance + evidence — never bare facts)
- Domain skills (implementing `SkillContract`)
- Domain tools (implementing `ToolContract`)
- Domain workflows (using `WorkflowGraph`)
- Domain policies (extending `PolicyRuleSet`)
- Domain memory domains (isolated namespaces)
- Domain prompt templates

A specialization MUST NOT:
- Modify `src/albra_seed/*`
- Reimplement runtime, memory, doctor, security, firewall, audit, evidence, provenance, MCP, handoff, observability, autonomy, resilience, or workflow engine
- Hardcode external providers as required dependencies

See [`docs/SPECIALIZATION.md`](SPECIALIZATION.md) for the full contract.
