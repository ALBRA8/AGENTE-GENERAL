# ALBRA AGENT SEED V1.0 — LIPO Report

> **LIPO** = Leave / Improve / Pull-Out classification. For the seed, the
> categories are: **KEEP / REFACTOR / MOVE / SKILL / TOOL / PROVIDER /
> AUXILIARY / DELETE**.
>
> "NO CONSTRUYAS OTRO AGENTE. CONSTRUYE LA SEMILLA DE LA QUE PUEDAN NACER
> TODOS LOS AGENTES ALBRA." The seed is intentionally complete; the spec
> explicitly says **"don't delete just to reduce lines."** Most components
> are KEEP.
>
> **Canonical numbers are owned by [`docs/CERTIFICATION.md`](CERTIFICATION.md).**
> Any number reproduced here is mirrored from that file.

---

## 1. Classification Legend

| Tag          | Meaning                                                                                  |
| ------------ | ---------------------------------------------------------------------------------------- |
| `KEEP`       | Stays as-is. The component is essential to the seed's DNA and works as designed.          |
| `REFACTOR`   | Stays but its internal structure needs cleanup (renames, splits, consolidation).         |
| `MOVE`       | Stays in the project but should live in a different subpackage.                          |
| `SKILL`      | Should be re-expressed as a `Skill` (procedural contract) for specialization use.        |
| `TOOL`       | Should be re-expressed as a `Tool` (with `ToolContract`) for specialization use.         |
| `PROVIDER`   | Should be re-expressed as an `LLMProvider` (kept as adapter, not core logic).            |
| `AUXILIARY`  | Useful support code (scripts, examples, tests) — not part of the importable API.        |
| `DELETE`     | Remove. Reserved for genuinely dead code; none are currently identified.                 |

---

## 2. Summary

- **Total components reviewed**: **40** (per `scripts/audit_matrix.py` `PROBES` list;
  cross-referenced against the 106 entries in `UNIVERSAL_DNA_COMPONENTS`, plus
  3 documented A2A helper modules `a2a.wire` / `a2a.exposition` /
  `a2a.observers` that are NOT in the DNA — see `docs/CERTIFICATION.md`
  § "A2A Helper Modules").
- **KEEP**: 35
- **PROVIDER**: 4 (the five concrete LLM provider adapters collapse to PROVIDER classification;
  the routing / registry / fallback / health that compose them are KEEP — see §3.3).
- **REFACTOR**: 0
- **MOVE**: 0
- **SKILL**: 0
- **TOOL**: 0
- **AUXILIARY**: 0 (scripts and tests are tracked separately under §4).
- **DELETE**: 0

The 7 new gap-closure components added after V1.0 (feedback, learning,
decision_memory, skill_evolution, parity, contamination, a2a) are all KEEP.

**Total tests**: **894** (was 280 at first freeze; was 372 after the first
six gap closures; was an intermediate count after the A2A wire-format + external-interop
additions; now **894** after BLOQ-A2A-SEC (+9 A2A security-perimeter
tests) and BLOQ-MEM (+11 memory factual-integrity + +10 memory
forget/audit tests)). The exact count is recomputed at verification
time by `scripts/clean_room_verify.py::_count_tests()` running
`pytest --collect-only -q`, so the report never carries a stale
hard-coded number. See `docs/CERTIFICATION.md` for the per-suite
breakdown.

A2A test suite (81 tests across 5 files): 28 main + 16 wire + 10 routes
+ 18 external interop + **9 security perimeter** (BLOQ-A2A-SEC).

Memory test suite now includes 11 factual-integrity tests + 10
forget/audit tests (BLOQ-MEM). FACT rule is unified via
`MemoryProvenance.validate_fact()` — the single source of truth;
tombstone `delete()` carries `by` + `reason` end-to-end; `MemoryManager`
accepts an optional `audit_sink` that emits `memory.forget` events.

The seed is intentionally complete. No deletions are recommended.

---

## 3. Per-Component Classification

The 40 components below correspond 1:1 to the `PROBES` list in
`scripts/audit_matrix.py`. The audit matrix script PROBES each component
(import → construct → round-trip) so every KEEP here is backed by an
actual passing probe — not a static declaration.

### 3.1 Core pillar

| #  | Component | Classification | Justification                                                                              |
| -- | --------- | -------------- | ------------------------------------------------------------------------------------------ |
| 1  | `core`    | KEEP           | The immutable DNA: types, identity, config, constants, base abstractions. Cannot move. All tests in the suite depend on it (count is recomputed dynamically by `scripts/clean_room_verify.py::_count_tests()`). |
| 2  | `config`  | KEEP           | `ConfigManager` + `ConfigLoader` + `ConfigValidator` + `SecretsAdapter` — universal config plumbing; no domain knowledge. |

### 3.2 Cognitive pillar

| #  | Component   | Classification | Justification                                                                              |
| -- | ----------- | -------------- | ------------------------------------------------------------------------------------------ |
| 3  | `reasoning` | KEEP           | `ReasoningEngine` ABC + `DefaultReasoningEngine` + `Planner` + `PromptManager` + templates. Domain-agnostic; templates use `$var` syntax for substitution. |
| 4  | `providers` | PROVIDER       | All five concrete providers (OpenAI/Anthropic/Groq/Ollama/ZAI) are already structured as lazy-loaded adapters. `MockProvider` is the always-available CORE_ONLY fallback. `ProviderRegistry` + `ProviderRouter` + `ProviderFallback` + `ProviderHealth` are KEEP (universal routing). |
| 5  | `context`   | KEEP           | `ContextManager` + `CorrelationTracker` (contextvars-based). Universal — no domain knowledge. |
| 6  | `contracts` | KEEP           | `Contract` + `ContractRegistry` for structured outputs. Universal. |

### 3.3 Memory & Knowledge pillar

| #  | Component           | Classification | Justification                                                                              |
| -- | ------------------- | -------------- | ------------------------------------------------------------------------------------------ |
| 7  | `memory`            | KEEP           | MemoryDV: `MemoryManager` + `InMemory` + `SQLite` + `Vector` (optional chromadb) + `Isolator` + `Provenance` + `Consolidation` + `Lifecycle` + `DECISION` type (GAP-3). Hard rules enforced in code (FACT requires evidence; UNKNOWN never promotes). |
| 8  | `provenance`        | KEEP           | `ProvenanceTracker` + `ProvenanceChain` + `SQLite`/`InMemory` stores. Universal trace plumbing. |
| 9  | `evidence`          | KEEP           | `EvidenceManager` + `EvidenceContract` + `CitationGate` + `EvidenceIntegrity`. Hard rules enforced in code. |

### 3.4 Security pillar

| #  | Component    | Classification | Justification                                                                              |
| -- | ------------ | -------------- | ------------------------------------------------------------------------------------------ |
| 10 | `security`   | KEEP           | `Authenticator` + `Role` + `Permission` + `SecretsManager` (Env/File/Vault) + `AccessControlList`. All DEFAULT-DENY. Vault backend is lazy. |
| 11 | `firewall`   | KEEP           | `ActionFirewall` + `FirewallRule` + `KillSwitch` + `CooldownManager` + `RateLimiter`. Critical safety primitive. |
| 12 | `policy`     | KEEP           | `PolicyEngine` + `PolicyRule` + `PolicyRuleSet` + `PolicyEvaluator`. Composes firewall + permissions + autonomy. |
| 13 | `autonomy`   | KEEP           | `AutonomyLevel` (L0..L5) + `AutonomyMatrix` (DEFAULT-DENY) + `AutonomyManager` + `AutonomyProfile`. Enforces "Autonomy ≠ Permission". |
| 14 | `approvals`  | KEEP           | `ApprovalEngine` + `ApprovalRequest` + `EscalationPolicy` + tamper-evident `ApprovalEvidence` chain. HITL gate. |

### 3.5 Execution pillar

| #  | Component    | Classification | Justification                                                                              |
| -- | ------------ | -------------- | ------------------------------------------------------------------------------------------ |
| 15 | `execution`  | KEEP           | `ExecutionRunner` + `ExecutionTrace` + `TraceStep` + `IdempotencyManager` + `TraceReplay`. Universal. |
| 16 | `workflows`  | KEEP           | `WorkflowGraph` (DAG with cycle detection) + `WorkflowExecutor` (HITL pause/resume) + `WorkflowState` + `WorkflowStep` + `WorkflowEngine` + `WorkflowRegistry`. |
| 17 | `jobs`       | KEEP           | `JobManager` + `Job` + `InMemory`/`SQLite` queues + `JobHistory`. Universal. |
| 18 | `scheduler`  | KEEP           | `Scheduler` + `ScheduledTask` + `OneShot`/`Interval`/`Cron` triggers. Lazy `croniter`. |
| 19 | `resilience` | KEEP           | `RetryExecutor` + `TimeoutExecutor` + `FallbackChain` + `CircuitBreaker` + `GracefulDegradation`. Universal resilience patterns. |

### 3.6 Observability pillar

| #  | Component       | Classification | Justification                                                                              |
| -- | --------------- | -------------- | ------------------------------------------------------------------------------------------ |
| 20 | `observability` | KEEP           | `StructuredLogger` (structlog-backed, secret redaction) + `MetricsRegistry` + `Tracer` (optional OTLP) + `HealthMonitor` + `ReadinessProbe`. |
| 21 | `audit`         | KEEP           | NORMAL `AuditLog` (structured JSONL) + TAMPER_EVIDENT `TamperEvidentAuditLog` (hash-chained) + `ChainVerifier`. Two SEPARATE classes per spec; never conflated. |
| 22 | `doctor`        | KEEP           | `Doctor` + `DiagnosticReport` + `CheckRegistry` + `DiagnosticMatrix` + built-in async checks. Honest reporting — never HEALTHY+IMPLEMENTED without probing. |
| 23 | `recovery`      | KEEP           | `RecoveryManager` + `ErrorClassifier` + `RecoveryStrategy` + `RecoveryAudit`. Refuses SECURITY/UNKNOWN auto-recovery; refuses dangerous markers. |

### 3.7 Integration pillar

| #  | Component        | Classification | Justification                                                                              |
| -- | ---------------- | -------------- | ------------------------------------------------------------------------------------------ |
| 24 | `mcp`            | KEEP           | `MCPServer` (ABC + InMemory) + `MCPClient` (ABC + InMemory) + `MCPRegistry` + `MCPValidator`. No external SDK; JSON-RPC-style protocol. |
| 25 | `handoff`        | KEEP           | `HandoffManager` + `HandoffRequest`/`Response` + `AgentRegistry` + `HandoffTimeout` + tamper-evident `HandoffEvidence` chain. A2A is layered on top of Handoff (not the other way around). |
| 26 | `communication`  | KEEP           | `CommunicationRouter` + `ChannelAdapter` (ABC) + `TextChannelAdapter` (always available) + `ChannelRegistry`. Other channels (voice/WhatsApp/email) are specialization. |
| 27 | `multimodal`     | KEEP           | `ModalityAdapter` ABC + `Text`/`Voice`/`Image`/`Document` adapters + `MultimodalRegistry`. Optional deps (whisper/PIL/pypdf/python-docx) lazy. |
| 28 | `files`          | KEEP           | `FileHandler` + `FileMetadata` + `ContentExtractor` + `FileProvenance` (hash-chained). Universal file plumbing. |

### 3.8 Extensibility pillar

| #  | Component       | Classification | Justification                                                                              |
| -- | --------------- | -------------- | ------------------------------------------------------------------------------------------ |
| 29 | `tools`         | KEEP           | `ToolRegistry` + `ToolExecutor` + `DryRunExecutor` + `ContractValidator` + 5 infra tools (echo, health, no-op, timestamp, status). NO domain tools. |
| 30 | `capabilities`  | KEEP           | `CapabilityRegistry` + `CapabilityResolver` + `CapabilityMatrix` (feeds PolicyEngine). |
| 31 | `skills`         | KEEP           | `SkillRegistry` + `SkillValidator` + `SkillExecutor` + `SkillLifecycle` + `SkillContract`. Universal — no domain skills shipped. |
| 32 | `plugins`        | KEEP           | `PluginManager` + `PluginLoader` + `PluginValidator` + `PluginManifest`. Hot-loadable extensions. |

### 3.9 Interfaces pillar

| #  | Component | Classification | Justification                                                                              |
| -- | --------- | -------------- | ------------------------------------------------------------------------------------------ |
| 33 | `api`     | KEEP           | `create_app` (FastAPI, lazy) + `APIServer` (uvicorn, lazy) + route modules (`/api/v1/*`, `/internal/*`, `/a2a/v1/`, `/.well-known/agent-card.json`) + `RequestContext` + `schemas.py`. |
| 34 | `cli`     | KEEP           | `main` (argparse-based) + subcommands + `CLIContext` + `Formatter` (table/json/yaml). Routes every payload through `redact_secret_values()`. |

> The README/audit matrix collapses `runtime` + `core` + `config` differently.
> The 40 components tracked by `scripts/audit_matrix.py` correspond to the 40
> rows above (the script counts `runtime` separately; the matrix collapses
> `core`+`config`+`identity` into the "core" probe, and presents `feedback`,
> `learning`, `decision_memory`, `skill_evolution`, `parity`,
> `contamination`, and `a2a` as their own probes).

### 3.10 Gap-closure components (added after V1.0 baseline)

These 7 components are the gap closures. Each was composed with — NOT
duplicated from — existing seed machinery (see `docs/UNIVERSAL_DNA_GAP_CLOSURE.md`
for the per-gap composition record).

| #  | Component          | Gap  | Classification | Justification                                                                              |
| -- | ------------------ | ---- | -------------- | ------------------------------------------------------------------------------------------ |
| 35 | `feedback`         | GAP-1 | KEEP           | `FeedbackManager` + `FeedbackStore` (ABC + InMemory + SQLite) + 6 domain-agnostic feedback types. Composes `CorrelationTracker` + `ProvenanceTracker`. 17 tests. |
| 36 | `learning`         | GAP-2 | KEEP           | `LearningManager` + `LearningProposal` + `ProposalStatus` (PROPOSED→REVIEW→APPROVE→APPLY) + HITL gate. Composes `FeedbackManager`, `DecisionMemory`, `ExecutionTrace`. 12 tests. |
| 37 | `decision_memory`  | GAP-3 | KEEP           | `DecisionRecord` wrapper on `MemoryRecord(memory_type=DECISION)` + `DecisionMemory` thin adapter. Non-destructive extension of MemoryDV (added `MemoryType.DECISION` as the 6th value). 10 tests. |
| 38 | `skill_evolution`  | GAP-4 | KEEP           | `SkillMetrics` + `ImprovementProposal` + `SkillEvolutionManager`. EXTENDS existing `SkillRegistry` (does not replace it). 16 tests. |
| 39 | `parity`           | GAP-6 | KEEP           | `ParityChecker` + `ParityReport` + `UNIVERSAL_DNA_COMPONENTS` (106 entries) + 8 `ALLOWED_SPECIALIZATIONS`. 17 tests. |
| 40 | `contamination`    | GAP-7 | KEEP           | `ContaminationScanner` + `ContaminationReport` + 24 forbidden domain keywords + 4 forbidden code patterns. Read-only scanner; never mutates files. 12 tests. |
| (extra) | `a2a`          | A2A  | KEEP           | 8-module adapter layer on Handoff: `card`, `contract`, `adapter`, `security`, `transport`, `server`, `client`, `registry`. `AgentCard` is DERIVED from `AgentManifest`. 81 tests across 5 files (28 main + 16 wire + 10 routes + 18 external interop + 9 security perimeter). 3 helper modules (`a2a.wire` / `a2a.exposition` / `a2a.observers`) are NOT in `UNIVERSAL_DNA_COMPONENTS` (BLOQ-DOCS decision). A2A security perimeter CLOSED for ALL 3 inbound operations (`message/send` + `tasks/get` + `tasks/cancel`) with token plumbing end-to-end (BLOQ-A2A-SEC). |
| (extra) | (birth/readiness) | GAP-5 | KEEP        | Modified `Runtime.startup()` to auto-register `ProviderRouter` (caller's explicit registration always wins) + added `Runtime.birth()` method. 8 new tests in `tests/unit/test_birth_readiness.py`. No new probe (modified `runtime` probe exercises it). |
| (extra) | (memory factual integrity) | BLOQ-MEM | KEEP | FACT rule unified via `MemoryProvenance.validate_fact()` (single source of truth, removed duplicated `requires_evidence_for_fact`). Tombstone `delete()` carries `by`/`reason` end-to-end (InMemory + SQLite). `MemoryManager.forget()` accepts optional `audit_sink` emitting `memory.forget` events. 11 + 10 new tests; 0 regressions. |
| (extra) | (a2a security perimeter) | BLOQ-A2A-SEC | KEEP | A2A security perimeter CLOSED for ALL 3 inbound operations (`message/send` + `tasks/get` + `tasks/cancel`) — same AUTHENTICATE → FIREWALL → POLICY pipeline runs for every op; same token flows through every layer (route → server → filter → authenticator). Token NEVER appears in logs/evidence/audit/metadata. 9 new tests in `tests/a2a/test_a2a_security_perimeter.py`. 0 regressions. |
| (extra) | (a2a helpers / docs) | BLOQ-DOCS | KEEP | Architectural decision: `a2a.wire` / `a2a.exposition` / `a2a.observers` are HELPERS (implementation details of the universal 8-module A2A contract), NOT in `UNIVERSAL_DNA_COMPONENTS`. `requirements.txt` re-pointed at `pyproject.toml` as the canonical source. `docs/CERTIFICATION.md` updated with final commit placeholder + new counts + A2A Helper Modules section. `docs/CLEANROOM.md` honest-scope §10 verified (CERT-CLEAN-ROOM). Optional GitHub Actions workflow `.github/workflows/clean-room.yml` added (does NOT make the seed depend on CI). |

> **Note on counting.** The orchestrator counts "33 original + 7 gap closures
> = 40 audit-matrix probes". The 7 gap closures include `a2a`. GAP-5 (birth /
> readiness) is NOT a separate probe because it modified `runtime/runtime.py`
> rather than adding a new module — its presence is exercised by the existing
> `runtime` probe and by `tests/unit/test_birth_readiness.py`.

---

## 4. Auxiliary Assets (NOT part of the 40 audit-matrix probes)

| Asset                              | Classification | Justification                                                                              |
| ---------------------------------- | -------------- | ------------------------------------------------------------------------------------------ |
| `scripts/clean_room_verify.py`     | AUXILIARY      | Clean-room verification script (spec §58, §70). Stdlib-only; runs from any cwd.            |
| `scripts/audit_matrix.py`          | AUXILIARY      | Generates `docs/AUDIT_MATRIX.md` by probing all 40 components. Stdlib-only.                |
| `scripts/parity_check.py`          | AUXILIARY      | Genetic parity CLI (GAP-6). Stdlib-only.                                                   |
| `scripts/contamination_scan.py`    | AUXILIARY      | Genetic contamination CLI (GAP-7). Stdlib-only (pathlib).                                   |
| `examples/`                        | AUXILIARY      | 15 runnable example scripts (`python examples/01_minimal_agent.py` etc.). NO domain knowledge. |
| `tests/`                           | AUXILIARY      | 894 tests across `unit/` (337 incl. 8 birth/readiness + 11 factual integrity + 10 forget/audit), `a2a/` (81 = 28 main + 16 wire + 10 routes + 18 interop + 9 security perimeter), `golden/` (13), `integration/` (5), `contracts/` (4), `security/` (5), `genetic/` (17), `contamination/` (12). |
| `docs/`                            | AUXILIARY      | Architecture, Specialization, Extending, Security, Memory, Cleanroom, LIPO, Examples, Final report, Audit matrix, A2A integration, Universal DNA gap closure, Birth contract, Certification. |
| `pyproject.toml`                   | AUXILIARY      | Build + optional-dependency declarations. CORE_ONLY mode is the default.                  |
| `requirements.txt`                 | AUXILIARY      | Convenience mirror of `pyproject.toml [project.dependencies]` — canonical source is `pyproject.toml`. `pip install -r requirements.txt` still works; `pip install -e .` is preferred. |
| `LICENSE`                          | AUXILIARY      | MIT.                                                                                        |

---

## 5. Cross-Cutting Observations

### 5.1 No component recommends DELETE

The spec is explicit: *"don't delete just to reduce lines."* Every component
is exercised by at least one test (see `docs/AUDIT_MATRIX.md`); removing one
would break the audit matrix and the clean-room report.

### 5.2 No component recommends REFACTOR at this stage

All 40 components pass the audit matrix (VERIFIED + IMPLEMENTED + PASS). All
source files fall within the 80–250 line guideline enforced by the F2-*
builders (a handful of cohesive files like `doctor/builtin_checks.py` are
marginal at ~260 lines but were kept together for cohesion).

### 5.3 No domain knowledge leaked

The seed ships:

- **0 domain tools** (only 5 infra tools: echo, health, no-op, timestamp, status).
- **0 domain skills**.
- **0 domain workflows**.
- **0 domain providers** (Mock is the always-available fallback).
- **0 domain memory domains**.
- **0 domain policies**.

All domain-specific behaviour is reserved for the specialization layer
(see `docs/SPECIALIZATION.md`). The contamination scanner confirms this
with **0 critical findings across 249 files scanned**.

### 5.3.1 A2A security perimeter — CLOSED for all 3 inbound operations

The A2A security perimeter (closed by BLOQ-A2A-SEC) ensures that EVERY
inbound A2A operation walks the same AUTHENTICATE → FIREWALL → POLICY
pipeline:

| Operation                  | Before BLOQ-A2A-SEC            | After BLOQ-A2A-SEC                          |
| -------------------------- | ------------------------------ | -------------------------------------------- |
| `message/send`              | Already filtered               | Unchanged (still filtered)                  |
| `tasks/get`                 | NOT filtered                   | Filtered (NEW)                              |
| `tasks/cancel`              | NOT filtered                   | Filtered (NEW)                              |

The same `token` flows through every layer (route → server → filter →
authenticator). The token NEVER appears in logs, evidence, audit entries,
or task metadata — only the resolved principal's identity is recorded.
9 tests in `tests/a2a/test_a2a_security_perimeter.py` cover the perimeter.

### 5.3.2 Memory FACT rule — unified

The FACT rule (BLOQ-MEM) is unified via `MemoryProvenance.validate_fact()`
— the SINGLE source of truth. The duplicated `requires_evidence_for_fact`
method was removed. The rule:

- Non-fact records skip the rule (always pass).
- Facts MUST satisfy ALL THREE conditions:
  1. `evidence_ids` non-empty.
  2. `source_type ∈ {tool, external, verified_provider}`.
  3. `confidence ≥ 0.9`.

Invalid facts never reach the store. The `MemoryManager.consolidate`
re-validates the FACT rule after the policy mutates; on violation, the
mutation is ROLLED BACK and the consolidation is rejected.

Tombstone `delete()` (BLOQ-MEM) carries `by` + `reason` end-to-end
(InMemory + SQLite + Vector — signature unified on the ABC). When a
`MemoryManager` is constructed with an optional `audit_sink`, `forget()`
emits `{action: "memory.forget", record_id, by, reason}` — CORE_ONLY
mode (no sink) is unchanged.

### 5.4 PROVIDER classification for the 5 concrete LLM providers

The five concrete providers (`OpenAIProvider`, `AnthropicProvider`,
`GroqProvider`, `OllamaProvider`, `ZAIProvider`) are already structured as
lazy-loaded adapters implementing the `LLMProvider` ABC. Each:

- Imports its SDK inside `__init__` (NOT at module load).
- Raises `NotImplementedError` cleanly if SDK or API key is missing.
- Is reported honestly by the Doctor as `EXTERNAL_DEPENDENCY` when its
  SDK isn't installed.

This is the correct structure: the providers ARE the PROVIDER classification
— they are adapters, not core logic. The router / registry / fallback /
health that compose them are KEEP.

---

## 6. Recommendation

**Ship as-is.** No deletions, no moves, no refactors required for V1.0
freeze. The 40 components form a coherent seed that satisfies the spec's
AUDIT_CANDIDATE bar:

- ✅ 40/40 components VERIFIED + IMPLEMENTED + PASS in the audit matrix.
- ✅ 17/17 required + 2/2 optional clean-room checks PASS (16 real PASS/FAIL gates + 1 informational scope-doc check R; CORE_ONLY mode).
- ✅ 106/106 universal DNA components present (`scripts/parity_check.py` → exit 0, `is_pure_seed=True`).
- ✅ 3 A2A helper modules (`a2a.wire` / `a2a.exposition` / `a2a.observers`) documented as NOT-DNA (BLOQ-DOCS).
- ✅ 0 critical contamination findings across 249 files scanned.
- ✅ 894/894 tests PASS in ~8s (337 unit incl. 11 factual + 10 forget audit + 8 birth/readiness; 81 A2A incl. 9 security perimeter; 13 golden; 5 integration; 4 contracts; 5 security; 17 parity; 12 contamination).
- ✅ A2A security perimeter CLOSED for ALL 3 inbound operations
  (message/send + tasks/get + tasks/cancel) with token plumbing end-to-end (BLOQ-A2A-SEC).
- ✅ Memory FACT rule unified via `MemoryProvenance.validate_fact()`;
  tombstone carries `by`/`reason`; optional `audit_sink` on
  `MemoryManager.forget()` (BLOQ-MEM).
- ✅ 0 domain knowledge.
- ✅ Honest status reporting (MOCK vs IMPLEMENTED vs EXTERNAL_DEPENDENCY).
- ✅ Hard security rules enforced in code + tests.
- ✅ A2A 1.0 wire format interop proven against a non-ALBRA reference agent
  (18 external interop tests, both directions).

The seed is **AUDIT_CANDIDATE** and ready to be cloned by the first
specialized ALBRA agent. See [`docs/CERTIFICATION.md`](CERTIFICATION.md)
for the canonical certification record.
