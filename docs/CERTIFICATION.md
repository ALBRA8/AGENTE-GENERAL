# ALBRA AGENT SEED V1.0 — Certification Status

> **This is the canonical single source of truth for the ALBRA AGENT SEED V1.0
> certification state.** All other docs (README.md, FINAL_REPORT.md,
> LIPO_REPORT.md, AUDIT_MATRIX.md, UNIVERSAL_DNA_GAP_CLOSURE.md,
> A2A_INTEGRATION.md) MUST reference this file rather than carrying their own
> copy of the numbers. When any number here changes, all referencing docs are
> out of date until refreshed from this file.

**Last verified:** 2026-10-11
**Commit:** `8af913e0f6b84a8d159e49e992870f1bacfede90`
**Status:** `READY_TO_FREEZE`

---

## Canonical Numbers

Every value in this table was produced by running the actual verification
script or by counting actual files. None are invented.

| Metric                       | Value | Source                                                                  |
| ---------------------------- | ----- | ----------------------------------------------------------------------- |
| Universal DNA components     | 106   | `src/albra_seed/parity/dna.py` `UNIVERSAL_DNA_COMPONENTS`               |
| Allowed specializations      | 8     | `src/albra_seed/parity/dna.py` `ALLOWED_SPECIALIZATIONS`                |
| Audit matrix probes          | 40    | `scripts/audit_matrix.py` `PROBES` list (33 original + 7 gap closures)  |
| Total tests                   | 444   | `python -m pytest tests/ --collect-only -q`                             |
| A2A tests                    | 72    | `python -m pytest tests/a2a/ --collect-only -q` (28 wire + 16 wire + 10 routes + 18 external interop) |
| A2A tests — main             | 28    | `tests/a2a/test_a2a.py`                                                 |
| A2A tests — wire format      | 16    | `tests/a2a/test_a2a_wire.py`                                             |
| A2A tests — routes           | 10    | `tests/a2a/test_a2a_routes.py`                                           |
| A2A tests — external interop | 18    | `tests/a2a/test_external_interop.py`                                    |
| Golden flows                 | 13    | `tests/golden/test_golden_flows.py`                                     |
| Integration tests            | 5     | `tests/integration/test_runtime_integration.py`                         |
| Contract tests               | 4     | `tests/contracts/test_contracts.py`                                     |
| Security tests               | 5     | `tests/security/test_security.py`                                       |
| Genetic parity tests         | 17    | `tests/genetic/test_parity.py`                                          |
| Genetic contamination tests  | 12    | `tests/contamination/test_contamination.py`                             |
| Birth / readiness tests      | 8     | `tests/unit/test_birth_readiness.py`                                    |
| Unit tests (total)           | 316   | `python -m pytest tests/unit/ --collect-only -q`                        |
| Unit tests (excl. birth/readiness) | ~308 | 316 − 8                                                               |

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

## Gate Results (verified 2026-10-11)

| Gate                       | Result | Evidence                                                                              |
| -------------------------- | ------ | ------------------------------------------------------------------------------------- |
| Tests                       | PASS   | `python -m pytest tests/ -q` → **444 passed in 8.08s**                                |
| Audit Matrix               | PASS   | `python scripts/audit_matrix.py` → **40/40 VERIFIED**                                 |
| Genetic Parity             | PASS   | `python scripts/parity_check.py` → exit 0, `is_pure_seed=True`, 106/106 DNA present   |
| Genetic Contamination      | PASS   | `python scripts/contamination_scan.py` → exit 0, `is_clean=True`, 0 critical, 0 warnings, 249 files scanned |
| Clean-room                 | PASS   | `python scripts/clean_room_verify.py` → **17/17 required + 2/2 optional** PASS (16 real PASS/FAIL gates + 1 informational scope-doc check R; see Clean-room scope §) |
| CORE_ONLY birth            | PASS   | Birth check inside `clean_room_verify.py` (check #3 + #4) + `tests/unit/test_birth_readiness.py` (8 tests) |
| A2A wire format             | PASS   | `pytest tests/a2a/test_a2a_wire.py` → **16 passed**                                   |
| A2A routes                  | PASS   | `pytest tests/a2a/test_a2a_routes.py` → **10 passed**                                 |
| A2A external interop        | PASS   | `pytest tests/a2a/test_external_interop.py` → **18 passed** via `httpx.ASGITransport` |
| A2A full suite              | PASS   | `pytest tests/a2a/ -q` → **72 passed in 0.85s**                                       |

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
| 5  | `memory`           | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/unit/test_memory.py`              | InMemory + SQLite + Vector (chromadb optional) + isolator + provenance + DECISION type (GAP-3)                                  |
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
| 40 | `a2a`              | VERIFIED | IMPLEMENTED     | PASS | MEDIUM   | `tests/a2a/test_a2a.py` (+ 3 sibling files) | A2A adapter layer on Handoff; AgentCard derived from Manifest; InProcessTransport always available; HTTPTransport lazy httpx   |

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
  `src/albra_seed/a2a/` (8 modules: `card`, `contract`, `adapter`,
  `security`, `transport`, `server`, `client`, `registry`).
- **Execution / Evidence / Provenance / Audit** = shared transversal
  infrastructure. ALL three integration channels (MCP, Handoff, A2A) feed
  into the same execution runner, evidence manager, provenance tracker,
  and audit log. No channel has its own parallel spine.

The separation is enforced by the parity check (`scripts/parity_check.py`)
and by the contamination check (`scripts/contamination_scan.py`).

---

## Final Status

```
READY_TO_FREEZE
```

All gates pass. The seed may be cloned to begin specialization work.
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
5. `pytest tests/` passes (444 tests).
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
pip install -e .[dev]

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
pip install -e .[dev]  # or: pip install -e . + pytest + httpx + fastapi
python scripts/clean_room_verify.py    # 17/17 required + 2/2 optional PASS
python scripts/audit_matrix.py         # 40/40 VERIFIED (writes docs/AUDIT_MATRIX.md)
python scripts/parity_check.py         # exit 0, is_pure_seed=True, 106/106 DNA
python scripts/contamination_scan.py   # exit 0, is_clean=True, 0 critical
python -m pytest tests/ -q             # 444 passed
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
