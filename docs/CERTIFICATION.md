# ALBRA AGENT SEED V1.0 — Certification Status

> **This is the canonical single source of truth for the ALBRA AGENT SEED V1.0
> certification state.** All other docs (README.md, FINAL_REPORT.md,
> LIPO_REPORT.md, AUDIT_MATRIX.md, UNIVERSAL_DNA_GAP_CLOSURE.md,
> A2A_INTEGRATION.md) MUST reference this file rather than carrying their own
> copy of the numbers. When any number here changes, all referencing docs are
> out of date until refreshed from this file.

**Last verified:** 2026-10-14
**Verified code commit:** `9f838defc4133be246d54c9e5272d3a8a97951ad` (security remediation branch)
**Status:** `CODE GATES PASS — PRODUCTION NOT VERIFIED`

> **Verification evidence (2026-10-14):** GitHub Actions run [38542685681](https://github.com/ALBRA8/AGENTE-GENERAL/actions/runs/38542685681) passed on Python 3.10, 3.11, and 3.12 for clean-room verification, audit matrix, parity, contamination scan, and full pytest. The full suite reported 624 passed and 1 warning on Python 3.11; all three Python-version jobs passed. This certifies the tested code commit above, not production deployment. The separate dashboard reported at `localhost:9000` is not included in this repository, so its `localStorage` handling and provider-call path remain NOT VERIFIED; see [`WEB_DASHBOARD_SECURITY.md`](WEB_DASHBOARD_SECURITY.md). Do not mark production ready or perform a final freeze until that dashboard is audited and the independent real-environment security/HITL acceptance is signed off.

---

## Canonical Numbers

The top-level test total and gate results below are verified against the code
commit and CI run identified above. Detailed per-suite breakdowns in this
table are historical figures unless explicitly re-counted; do not treat those
subgroup counts as current certification evidence.

| Metric                       | Value | Source                                                                  |
| ---------------------------- | ----- | ----------------------------------------------------------------------- |
| Universal DNA components     | 106   | `src/albra_seed/parity/dna.py` `UNIVERSAL_DNA_COMPONENTS`               |
| A2A helper modules (NOT DNA)| 3   | `a2a.wire`, `a2a.exposition`, `a2a.observers` — see § "A2A Helper Modules" |
| Allowed specializations      | 8     | `src/albra_seed/parity/dna.py` `ALLOWED_SPECIALIZATIONS`                |
| Audit matrix probes          | 40    | `scripts/audit_matrix.py` `PROBES` list (33 original + 7 gap closures)  |
| Total tests                   | 624   | `python -m pytest tests/ -q` on verified code commit                             |
| A2A tests                    | 81    | `python -m pytest tests/a2a/ --collect-only -q` (28 main + 16 wire + 10 routes + 18 external interop + 9 security perimeter) |
| A2A tests — main             | 28    | `tests/a2a/test_a2a.py`                                                 |
| A2A tests — wire format      | 16    | `tests/a2a/test_a2a_wire.py`                                             |
| A2A tests — routes           | 10    | `tests/a2a/test_a2a_routes.py`                                           |
| A2A tests — external interop | 18    | `tests/a2a/test_external_interop.py`                                    |
| A2A tests — security perimeter | 9   | `tests/a2a/test_a2a_security_perimeter.py` (BLOQ-A2A-SEC)               |
| Golden flows                 | 13    | `tests/golden/test_golden_flows.py`                                     |
| Integration tests            | 5     | `tests/integration/test_runtime_integration.py`                         |
| Contract tests               | 4     | `tests/contracts/test_contracts.py`                                     |
| Security tests               | 5     | `tests/security/test_security.py`                                       |
| Genetic parity tests         | 17    | `tests/genetic/test_parity.py`                                          |
| Genetic contamination tests  | 12    | `tests/contamination/test_contamination.py`                             |
| Memory factual integrity     | 11    | `tests/unit/test_memory_factual_integrity.py` (BLOQ-MEM)                |
| Memory forget / audit        | 10    | `tests/unit/test_memory_forget_audit.py` (BLOQ-MEM)                     |
| Birth / readiness tests      | 8     | `tests/unit/test_birth_readiness.py`                                    |
| Unit tests (total)           | Historical; not re-counted in this CI run | Re-run `python -m pytest tests/unit/ --collect-only -q` before using subgroup totals |
| Unit tests (excl. birth/readiness) | Historical; not re-counted | Recompute from current collection output |

### Allowed specializations (8)

```
specialization.identity
specialization.knowledge
specialization.skills
specialization.tools
specialization.workflows
specialization.policies
specialization.memory
specialization.prompts
```

### Gap-closure probes (7) added to the audit matrix

| Probe             | Gap | Added by         | Test file                                |
| ----------------- | --- | ---------------- | ---------------------------------------- |
| `feedback`        | 1   | GAP-1 builder    | `tests/unit/test_feedback.py`            |
| `learning`        | 2   | GAP-2 builder    | `tests/unit/test_learning.py`            |
| `decision_memory` | 3   | GAP-3 builder    | `tests/unit/test_decision_memory.py`      |
| `skill_evolution` | 4   | GAP-4 builder    | `tests/unit/test_skill_evolution.py`     |
| `parity`          | 6   | GAP-6 builder    | `tests/genetic/test_parity.py`           |
| `contamination`   | 7   | GAP-7 builder    | `tests/contamination/test_contamination.py` |
| `a2a`             | A2A | A2A-IMPL builder | `tests/a2a/test_a2a.py` (+ 3 sibling files) |

(GAP-5 — Birth / Readiness Contract — modified `runtime/runtime.py` rather
than adding a new probe, so it does not appear as its own probe. It is
exercised by `tests/unit/test_birth_readiness.py` and by check #4 + #5 of
the clean-room script.)

---

## Gate Results (verified 2026-10-14)

| Gate                       | Result | Evidence                                                                              |
| -------------------------- | ------ | ------------------------------------------------------------------------------------- |
| Tests                       | PASS   | `python -m pytest tests/ -q` → **624 passed, 1 warning** (Python 3.11; CI run 38542685681; Python 3.10/3.12 also passed)                                  |
| Audit Matrix               | PASS   | `python scripts/audit_matrix.py` → **40/40 VERIFIED**                                 |
| Genetic Parity             | PASS   | `python scripts/parity_check.py` → exit 0, `is_pure_seed=True`, 106/106 DNA present   |
| Genetic Contamination      | PASS   | `python scripts/contamination_scan.py` → exit 0, `is_clean=True`, 0 critical, 0 warnings, 255 files scanned |
| Clean-room                 | PASS   | `python scripts/clean_room_verify.py` → **17/17 required + 2/2 optional** PASS (16 real PASS/FAIL gates + 1 informational scope-doc check R; see Clean-room scope §) |
| **CI (GitHub Actions)**     | **PASS** | workflow `.github/workflows/clean-room.yml` on commit `9f838de` → **3/3 jobs success** (Python 3.10, 3.11, 3.12 on ubuntu-latest); run_id `38542685681` |
| CORE_ONLY birth            | PASS   | Birth check inside `clean_room_verify.py` (check #3 + #4) + `tests/unit/test_birth_readiness.py` (8 tests) |
| A2A wire format             | PASS   | `pytest tests/a2a/test_a2a_wire.py` → **16 passed**                                   |
| A2A routes                  | PASS   | `pytest tests/a2a/test_a2a_routes.py` → **10 passed**                                 |
| A2A external interop        | PASS   | `pytest tests/a2a/test_external_interop.py` → **18 passed** via `httpx.ASGITransport` |
| A2A security perimeter      | PASS   | `pytest tests/a2a/test_a2a_security_perimeter.py` → **9 passed** (BLOQ-A2A-SEC; all 3 ops through filter, token plumbing end-to-end) |
| A2A full suite              | PASS   | `pytest tests/a2a/ -q` → **81 passed in ~2s**                                          |
| Security fail-open closure  | PASS   | Included in the verified **624-test full suite**; the updated per-file count was not separately recorded (BLOQ 1 + BLOQ 2; DEFAULT-DENY, authenticated RBAC, trace ownership, and no token leakage) |
| Memory FACT rule            | PASS   | `pytest tests/unit/test_memory_factual_integrity.py` → **11 passed** (BLOQ-MEM; unified via `MemoryProvenance.validate_fact`) |
| Memory forget / audit       | PASS   | `pytest tests/unit/test_memory_forget_audit.py` → **10 passed** (BLOQ-MEM; tombstone by/reason + audit_sink) |
| Browser dashboard key handling | NOT VERIFIED | Dashboard source is not tracked in this repository; inspect/fix `localStorage` and direct provider calls against the actual UI using `WEB_DASHBOARD_SECURITY.md` 
| Production HITL / infrastructure acceptance | NOT VERIFIED | Requires real evidence for encrypted storage/backups, KMS, auth/RBAC, firewall/egress, restore drill, uncertain-side-effect reconciliation, and kill switch |
| pyproject.toml parses       | PASS   | `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` (8 optional-dependencies: openai/anthropic/groq/vector/api/otel/dev/all) |

---

## Component Inventory (40 audit-matrix components)

The matrix is **auto-generated** by `scripts/audit_matrix.py` and saved to
[`docs/AUDIT_MATRIX.md`](AUDIT_MATRIX.md). Every row is the result of an
actual probe (import → construct → round-trip), not a static declaration.
The canonical regenerated table is reproduced here for offline reference;
the script is the authoritative source.

| #  | Component          | Status   | Implementation  | Test | Risk     | Evidence                                  | Notes (summary)                                                                                                                  |
| -- | ------------------ | -------- | --------------- | ---- | -------- | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| 1  | `core`             | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_config.py`              | Immutable DNA: types, identity, config, constants, base abstractions                                                            |
| 2  | `runtime`          | VERIFIED | IMPLEMENTED     | PASS | HIGH     | `tests/unit/test_runtime.py`             | Lifecycle + loop + state + agent + bootstrap of `provider_router` (GAP-5)                                                        |
| 3  | `reasoning`        | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_reasoning.py`           | Planner + engine + prompt templates (`$var` substitution)                                                                       |
| 4  | `providers`        | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_providers.py`            | Mock always available; OpenAI/Anthropic/Groq/Ollama/ZAI = NOT_INSTALLED (optional, lazy)                                         |
| 5  | `memory`           | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_memory.py` + `tests/unit/test_memory_factual_integrity.py` + `tests/unit/test_memory_forget_audit.py`              | InMemory + SQLite + Vector (chromadb optional) + isolator + provenance + DECISION type (GAP-3); FACT rule unified via `MemoryProvenance.validate_fact()` (BLOQ-MEM); tombstone carries `by` + `reason` on `delete()`; optional `audit_sink` on `MemoryManager.forget()` (BLOQ-MEM)                                  |
| 6  | `security`         | VERIFIED | IMPLEMENTED     | PASS | HIGH     | `tests/unit/test_security.py`            | Vault backend = EXTERNAL_DEPENDENCY (hvac not installed)                                                                         |
| 7  | `firewall`         | VERIFIED | IMPLEMENTED     | PASS | CRITICAL | `tests/unit/test_firewall.py`            | default-deny + kill switch + cooldown + rate limit                                                                              |
| 8  | `policy`           | VERIFIED | IMPLEMENTED     | PASS | HIGH     | `tests/unit/test_policy.py`              | Policy engine + evaluator + rules                                                                                               |
| 9  | `evidence`         | VERIFIED | IMPLEMENTED     | PASS | HIGH     | `tests/unit/test_evidence.py`            | contract + integrity + store + citation; UNKNOWN never auto-promoted                                                            |
| 10 | `provenance`       | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_provenance.py`          | tracker + chain + store                                                                                                         |
| 11 | `execution`        | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_execution.py`           | runner + trace + idempotency + replay                                                                                          |
| 12 | `audit`            | VERIFIED | IMPLEMENTED     | PASS | HIGH     | `tests/unit/test_audit.py`               | NORMAL (JSONL) + TAMPER_EVIDENT (hash-chained) — separate classes per spec                                                     |
| 13 | `doctor`           | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_doctor.py`              | Doctor + DiagnosticReport + DiagnosticMatrix + built-in checks                                                                  |
| 14 | `recovery`         | VERIFIED | IMPLEMENTED     | PASS | HIGH     | `tests/unit/test_recovery.py`            | classifier + strategies + manager; SECURITY errors never auto-recover                                                          |
| 15 | `mcp`              | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_mcp.py`                 | server + client + registry + validator; JSON-RPC-style protocol                                                                |
| 16 | `handoff`          | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_handoff.py`             | contract + registry + manager + timeout + tamper-evident evidence chain                                                         |
| 17 | `tools`            | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_tools.py`               | registry + executor + dry_run + contract_validator + 5 infra tools (NO domain tools)                                            |
| 18 | `capabilities`     | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_capabilities.py`        | registry + resolver + matrix                                                                                                    |
| 19 | `skills`           | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_skills.py`              | registry + executor + validator + lifecycle + contract                                                                          |
| 20 | `plugins`          | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_plugins.py`             | loader + validator + manager + manifest                                                                                        |
| 21 | `communication`    | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_communication.py`       | router + adapters + registry + message (TEXT adapter always available)                                                        |
| 22 | `multimodal`       | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_multimodal.py`          | text always ready; image/voice/document lazy-import optional deps                                                              |
| 23 | `files`            | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_files.py`               | handler + metadata + provenance + extractor                                                                                     |
| 24 | `workflows`        | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_workflows.py`           | graph + executor + state + step + registry                                                                                      |
| 25 | `jobs`             | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_jobs.py`                | manager + queue + sqlite_queue + job + history                                                                                  |
| 26 | `scheduler`        | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_scheduler.py`           | scheduler + task + trigger                                                                                                      |
| 27 | `resilience`       | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_resilience.py`          | retry + timeout + fallback + circuit_breaker + degradation                                                                      |
| 28 | `observability`    | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_observability.py`       | logger + metrics + tracer + health + readiness; otel = EXTERNAL_DEPENDENCY                                                     |
| 29 | `autonomy`         | VERIFIED | IMPLEMENTED     | PASS | HIGH     | `tests/unit/test_autonomy.py`            | L0..L5 + manager + profile (Autonomy ≠ Permission)                                                                              |
| 30 | `approvals`        | VERIFIED | IMPLEMENTED     | PASS | HIGH     | `tests/unit/test_approvals.py`           | engine + request + escalation + tamper-evident evidence                                                                         |
| 31 | `api`              | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_api.py`                 | create_app + routes + schemas + context + server (FastAPI lazy)                                                                 |
| 32 | `cli`              | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_cli.py`                 | argparse + 11 subcommands + CLIContext + Formatter                                                                              |
| 33 | `config`           | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/unit/test_config.py`              | manager + loader + validator + secrets adapter                                                                                 |
| 34 | `feedback`         | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_feedback.py`            | FeedbackManager + store (InMemory + SQLite) + 6 types (POSITIVE/NEGATIVE/CORRECTION/HUMAN/SYSTEM/AUTOMATED)                    |
| 35 | `learning`         | VERIFIED | IMPLEMENTED     | PASS | HIGH     | `tests/unit/test_learning.py`            | LearningManager + ProposalStatus pipeline (PROPOSED→REVIEW→APPROVE→APPLY) with HITL gate                                       |
| 36 | `decision_memory`  | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_decision_memory.py`     | DecisionRecord wrapper on MemoryRecord(memory_type=DECISION); non-destructive extension of MemoryDV                              |
| 37 | `skill_evolution`  | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_skill_evolution.py`     | SkillMetrics + ImprovementProposal + SkillEvolutionManager; extends existing SkillRegistry                                      |
| 38 | `parity`           | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/genetic/test_parity.py`           | ParityChecker + UNIVERSAL_DNA_COMPONENTS (106 entries) + ParityReport                                                            |
| 39 | `contamination`    | VERIFIED | IMPLEMENTED     | PASS | LOW      | `tests/contamination/test_contamination.py` | ContaminationScanner + 24 forbidden domain keywords + 4 code patterns                                                          |
| 40 | `a2a`              | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/a2a/test_a2a.py` (+ 4 sibling files: wire, routes, interop, security perimeter) | A2A adapter layer on Handoff; AgentCard derived from Manifest; InProcessTransport always available; HTTPTransport lazy httpx; security perimeter CLOSED for ALL 3 inbound ops (`message/send` + `tasks/get` + `tasks/cancel`) with token plumbing end-to-end (BLOQ-A2A-SEC)   |

Status distribution: `{VERIFIED: 40}`. No PARTIAL / MOCK / PLACEHOLDER /
EXTERNAL_DEPENDENCY / NOT_CONFIGURED / NOT_VERIFIED / MISSING rows.

---

## A2A Separation (clearly stated)

ALBRA AGENT SEED V1.0 ships four distinct integration channels plus a
shared infrastructure spine. They are NOT interchangeable — each has a
specific role and a specific contract:

- **MCP** = tools / resources / data. Contract: *Agente ↔ herramientas*.
  Used when the agent must invoke an external tool, expose one, or
  exchange data through a tool bus. Implementation: `src/albra_seed/mcp/`.
- **Handoff** = internal delegation contract. Contract:
  *Agente ALBRA ↔ Agente ALBRA interno*. Used when one ALBRA agent
  delegates a unit of work to another ALBRA agent inside the same trust
  boundary (same identity fabric, same audit chain, same evidence store).
  Implementation: `src/albra_seed/handoff/`.
- **A2A** = agent discovery / Agent Card / delegation / messaging.
  Contract: *Agente ↔ Agente externo compatible A2A 1.0*. Used when an
  ALBRA agent must interoperate with ANY A2A 1.0-compliant external agent
  (ALBRA or non-ALBRA). A2A is a pure translation layer over Handoff:
  `AgentCard` is DERIVED from `AgentManifest` (single source of truth);
  `A2ATask` is TRANSLATED into a `HandoffRequest`. Implementation:
  `src/albra_seed/a2a/` (8 universal DNA modules: `card`, `contract`,
  `adapter`, `security`, `transport`, `server`, `client`, `registry`
  + 3 helper modules: `wire`, `exposition`, `observers` — see §
  "A2A Helper Modules" below).
  The A2A security perimeter is CLOSED for ALL 3 inbound operations:
  `message/send`, `tasks/get`, `tasks/cancel`. The same
  AUTHENTICATE → FIREWALL → POLICY pipeline runs for every operation;
  the same token flows through every layer (route → server → filter →
  authenticator). The token NEVER appears in logs, evidence, audit
  entries, or task metadata — only the resolved principal's identity
  is recorded (BLOQ-A2A-SEC; 9 tests in
  `tests/a2a/test_a2a_security_perimeter.py`).
- **Execution / Evidence / Provenance / Audit** = shared transversal
  infrastructure. ALL three integration channels (MCP, Handoff, A2A) feed
  into the same execution runner, evidence manager, provenance tracker,
  and audit log. No channel has its own parallel spine.

The separation is enforced by the parity check (`scripts/parity_check.py`)
and by the contamination check (`scripts/contamination_scan.py`).

---

## A2A Helper Modules — NOT in UNIVERSAL_DNA_COMPONENTS

**Decision (BLOQ-DOCS, 2026-10-12):** the three A2A-adjacent modules
listed below are NOT in `UNIVERSAL_DNA_COMPONENTS` (in
`src/albra_seed/parity/dna.py`). They are implementation details of
the universal 8-module A2A contract (which IS in the DNA), NOT
universal capabilities themselves. A specialization MAY replace any
of them as long as the universal contract (`a2a.card`,
`a2a.contract`, `a2a.adapter`, `a2a.security`, `a2a.transport`,
`a2a.server`, `a2a.client`, `a2a.registry`) is preserved.

| Module           | Role                                                            | Why HELPER, not DNA                                                                                                                          |
| ---------------- | --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `a2a.wire`       | A2A 1.0 wire-format serialization helpers                       | The universal contract is "speak A2A 1.0" (carried by `a2a.transport` / `a2a.server` / `a2a.client`). `wire.py` is ONE binding of that contract. A different binding (e.g. protobuf, CBOR) could replace it without changing the universal DNA. |
| `a2a.exposition` | `build_a2a_server()` convenience wrapper                        | Pure composition helper. Callers may construct an `A2AServer` directly — the wrapper just composes `A2AServer` + `HandoffManager` into a single call. |
| `a2a.observers`  | Audit / provenance / evidence hooks called by `a2a.server`     | The universal infrastructure (`audit.*`, `provenance.*`, `evidence.*`) is already in `UNIVERSAL_DNA_COMPONENTS`. `observers.py` is the A2A-specific binding that wires those universal components into the A2A receive / dispatch lifecycle. |

### Architectural rule applied

- **Universal DNA component**: a capability that EVERY ALBRA agent
  inherits and uses. If you remove it, the agent can't do its job.
- **Helper**: an implementation detail. Other implementations could
  replace it without changing the contract.

All three modules above are exercised transitively by the `a2a`
audit-matrix probe and by the `tests/a2a/` evidence chain (81 tests
across 5 files, including the 9-test security perimeter suite in
`tests/a2a/test_a2a_security_perimeter.py`). The parity check
(`scripts/parity_check.py`) does NOT enumerate them, so removing one
does NOT flip `is_pure_seed` — but removing any of the 8 universal
A2A DNA modules DOES fail parity. The distinction is documented
inline in `src/albra_seed/parity/dna.py` (module docstring + comment
block above `UNIVERSAL_DNA_COMPONENTS`).

---

## Final Status

```
CODE_GATES_PASS — PRODUCTION_NOT_VERIFIED
```

Code-level CI gates passed on the verified code commit. Production readiness and final freeze remain blocked until the separate dashboard source is audited and fixed, and the independent real-environment security/HITL acceptance is signed off. The seed may be cloned for isolated development and testing only.
Specialization rules are in [`docs/SPECIALIZATION.md`](SPECIALIZATION.md).

---

## Clean-room Scope (honest)

`scripts/clean_room_verify.py` reports **17/17 required + 2/2 optional PASS**.
This breaks down as:

- **16 real PASS/FAIL gates** (#1 CLONE-LIKE through #16 DOCTOR-CHECK) —
  these run actual probes in the current process tree (import, construct,
  invoke, assert). Failure on any of them causes `CLEAN-ROOM: FAIL`.
- **1 informational check (R. REPRODUCIBILITY-NOTES)** — `required=True`
  but always passes. It prints inline what the script does and does NOT
  verify, so the scope is honest and discoverable in the report itself.
  It is NOT a gate.
- **2 optional checks** (#2b INSTALL-LIKE optional deps, #17 A2A-CHECK) —
  may SKIP without failing the overall PASS.

### What the script DOES verify (real, in this process tree)

1. Package imports cleanly from a fresh subprocess with sanitized env.
2. Required deps (`pydantic`, `structlog`, `dotenv`) are importable.
3. `SeedConfig.from_env()` → CORE_ONLY.
4. `Runtime.startup()` + `readiness() == True`.
5. `pytest tests/` passes (624 tests on the verified code commit).
6. `__seed_version__ == "ALBRA_SEED_V1.0"`.
7. `Agent.run("hello")` returns `AgentResponse`.
8. `runtime.health()` returns dict with `status` key.
9. `runtime.readiness() == True`.
10. All 13 golden flows pass.
11. FastAPI `create_app` + `TestClient` GET `/api/v1/health` returns 200
    (only when `fastapi` installed; else SKIP).
12. CLI `albra health` returns exit code 0.
13. Memory write/read/isolation (composite `agent_id + user_id + domain +
    context_id` key).
14. Audit chain append + tamper-evident verification.
15. Firewall default-deny.
16. `Doctor.diagnose()` returns `DiagnosticReport` with at least 6
    components (runtime, memory, providers, tools, audit, security).
17. (OPTIONAL) A2A `AgentCard` derivable from `AgentManifest`.

### What the script DOES NOT verify (needs external CI)

- **Fresh virtualenv creation** — assumes deps already installed via
  `pip install -e .` or equivalent. The script does NOT create a venv
  and does NOT run `pip install`.
- **Clone from a git remote** — assumes you are already inside the
  checkout you want to verify.
- **`pip install -e .` from scratch** — does NOT exercise the
  `pyproject.toml` install metadata, `entry_points`, `console_scripts`,
  or extras resolution.
- **Multiple Python version compatibility** — only the current
  interpreter is exercised.
- **Real network A2A interoperability** — the test suite uses
  `httpx.ASGITransport` (in-process), not live HTTP to a remote agent
  on another host.
- **External provider integration** (OpenAI / Anthropic / Groq / Ollama
  / ZAI) — CORE_ONLY mode only. Optional providers are checked for
  import-ability but never invoked with real credentials.

### External A2A interop — best-effort verification

The closest thing to real-network A2A interop that can be verified
without a live remote agent is `tests/a2a/test_external_interop.py`
(18 tests, all PASS). It spins up a non-ALBRA FastAPI fixture
implementing the A2A 1.0 wire format and talks to it via
`httpx.ASGITransport` (in-process, no real socket). Coverage:

- discover / send / get_status / cancel in both directions
  (ALBRA→Remote and Remote→ALBRA),
- ALBRA↔ALBRA end-to-end via HTTP,
- Security filter pipeline propagation,
- Wire format compatibility, malformed request handling, JSON-RPC
  −32601 for unknown methods,
- Artifact + correlation_id propagation,
- `HTTPTransport` raising clean `NotImplementedError` when `httpx` is
  missing.

This is the BEST that can be done in a single-process verification run.
True cross-host interop would require a CI job that deploys a second
agent on a separate machine.

### How to perform a FULL clean-room verification (external CI)

```bash
# 1. Fresh clone (NOT your working copy)
git clone https://github.com/ALBRA8/AGENTE-GENERAL.git fresh-checkout
cd fresh-checkout

# 2. Fresh venv
python -m venv .venv
source .venv/bin/activate        # Linux/macOS

# 3. Install from scratch (this is the part the script CANNOT verify
#    for itself — it assumes deps are already installed)
pip install -e .[dev,a2a,api]

# 4. Run the clean-room verification
python scripts/clean_room_verify.py
#    → expected: required: 17/17 passed; optional: 2/2 passed
#    → expected final line: CLEAN-ROOM: PASS
#    → exit code MUST be 0
```

See [`docs/CLEANROOM.md`](CLEANROOM.md) §10 for the full honest-scope
write-up.

---

## How to Re-verify

```bash
git clone https://github.com/ALBRA8/AGENTE-GENERAL.git
cd AGENTE-GENERAL
pip install -e .[dev,a2a,api]  # canonical install for full test coverage (CI uses this)
python scripts/clean_room_verify.py    # 17/17 required + 2/2 optional PASS
python scripts/audit_matrix.py         # 40/40 VERIFIED (writes docs/AUDIT_MATRIX.md)
python scripts/parity_check.py         # exit 0, is_pure_seed=True, 106/106 DNA
python scripts/contamination_scan.py   # exit 0, is_clean=True, 0 critical
python -m pytest tests/ -q             # 624 passed on verified code commit
```

All gates must PASS. If any gate fails, the seed is NOT ready to freeze;
consult the per-gate evidence column above.

---

## References

- [`docs/AUDIT_MATRIX.md`](AUDIT_MATRIX.md) — auto-generated 40-component matrix.
- [`docs/LIPO_REPORT.md`](LIPO_REPORT.md) — KEEP / REFACTOR / MOVE / SKILL / TOOL / PROVIDER / AUXILIARY / DELETE classification (40 components).
- [`docs/FINAL_REPORT.md`](FINAL_REPORT.md) — final implementation report (references this file).
- [`docs/UNIVERSAL_DNA_GAP_CLOSURE.md`](UNIVERSAL_DNA_GAP_CLOSURE.md) — 7-gap closure record (feedback, learning, decisions, skill evolution, birth, parity, contamination).
- [`docs/A2A_INTEGRATION.md`](A2A_INTEGRATION.md) — A2A adapter design (72 tests, 4 ops × 2 directions + ALBRA↔ALBRA).
- [`docs/BIRTH_CONTRACT.md`](BIRTH_CONTRACT.md) — CORE_ONLY birth contract.
- [`docs/CLEANROOM.md`](CLEANROOM.md) — clean-room verification protocol.
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — full architecture map.
- [`docs/SPECIALIZATION.md`](SPECIALIZATION.md) — specialization contract.
- [`docs/SECURITY.md`](SECURITY.md), [`docs/MEMORY.md`](MEMORY.md),
  [`docs/EXTENDING.md`](EXTENDING.md), [`docs/EXAMPLES.md`](EXAMPLES.md).
