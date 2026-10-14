# ALBRA AGENT SEED V1.0 — Clean-Room Verification Protocol

> La verificación clean-room garantiza que la semilla corre desde cero,
> sin cachés, sin credenciales externas, sin estado previo. Es el pasaporte
> de "INTEGRATION_READY" para los agentes especializados que clonen este repo.

This document explains what clean-room verification is, why it matters, the
16 required PASS/FAIL checks + 1 informational scope-doc check (R) performed
by `scripts/clean_room_verify.py`, how to interpret the PASS/FAIL report,
and how to re-run it after specialization.

It also documents **honestly** what the script does NOT verify — those
items would need an external CI (fresh venv, fresh git clone, real network
interop). See §10 "Honest Scope" below.

---

## 1. Why Clean-Room Matters

The seed is meant to be **cloned** by every future ALBRA agent (legal, sales,
research, trading, marketing, ...). Each clone must verify:

1. **CLONE-LIKE** — the seed runs from a fresh interpreter with no prior
   state, no cached imports, no env vars beyond `PYTHONPATH`.
2. **CORE_ONLY** — fully functional without external credentials (Mock
   provider used as fallback everywhere).
3. **DEFAULT-DENY** — security primitives block by default, allow by rule.
4. **TAMPER-EVIDENT** — audit chains detect tampering.
5. **EVIDENCE-DISCIPLINED** — UNKNOWN never becomes FACT.
6. **HONEST-REPORTING** — the Doctor reports IMPLEMENTED vs MOCK vs
   EXTERNAL_DEPENDENCY, never HEALTHY+IMPLEMENTED without probing.

A specialization that breaks any of these invariants is unsafe to deploy.
The clean-room script catches such breakages BEFORE the specialization
ships.

---

## 2. Running the Verification

From anywhere inside the cloned repo:

```bash
python scripts/clean_room_verify.py
```

The script:
- Bootstraps `sys.path` from its own location (works from ANY cwd).
- Uses ONLY the Python stdlib (no extra deps required to run the script).
- Spawns subprocesses with sanitized environments to simulate clone-like
  conditions.
- Runs each of the 16 PASS/FAIL checks + 1 informational check (R),
  printing `[PASS]` / `[FAIL]` / `[SKIP]` for each.
- Exits 0 if ALL required checks pass (including the informational R
  check, which always passes); exits 1 otherwise.

Expected output (final lines):

```
required: 17/17 passed; optional: 2/2 passed

CLEAN-ROOM: PASS
```

(The 17 required = 16 real PASS/FAIL checks + 1 informational scope-doc
check R; the 2 optional = 2b INSTALL-LIKE optional deps + 17 A2A-CHECK.)

---

## 3. The 17 Required Checks (16 PASS/FAIL + 1 Informational)

The table below lists every check produced by the script. The 16
PASS/FAIL checks (numbered 1–16) are real verification gates: failure
on any of them causes `CLEAN-ROOM: FAIL`. Check R is INFORMATIONAL —
`required=True` but always passes — and exists solely to make the
verification scope honest and discoverable in the report itself.

| #  | Name                          | What it verifies                                                                                                          | Required? |
| -- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------- | --------- |
| 1  | CLONE-LIKE                    | Fresh interpreter with `PATH` + `PYTHONPATH` only; `import albra_seed` succeeds and reports `__seed_version__`.           | yes       |
| 2  | INSTALL-LIKE (required deps)  | `pydantic`, `structlog`, `dotenv` import cleanly. **NOTE:** assumes they were already installed via `pip install -e .`; the script does NOT install from scratch (see §10). | yes       |
| 2b | INSTALL-LIKE (optional deps)  | `openai` / `anthropic` / `fastapi` / `uvicorn` / `httpx` reported as `OK` or `NOT_INSTALLED` without failing.             | no        |
| 3  | CONFIG-FROM-SCRATCH           | `SeedConfig.from_env()` in a subprocess with `ALBRA_*` and provider env vars stripped returns `is_core_only() == True`.    | yes       |
| 4  | INIT-FROM-SCRATCH              | `Runtime.startup()` + register `ProviderRouter(MockProvider)` → `readiness() == True`; then `shutdown()` cleanly.         | yes       |
| 5  | TEST-RUN                       | `pytest tests` runs cleanly: `N passed, 0 failed`. (Counts the entire suite — the exact count is recomputed dynamically by `scripts/clean_room_verify.py::_count_tests()` via `pytest --collect-only -q`, so it never goes stale; the number varies by which optional extras are installed.)                       | yes       |
| 6  | BUILD-CHECK                    | `python -c "import albra_seed; print(__seed_version__)"` returns `"ALBRA_SEED_V1.0"`.                                       | yes       |
| 7  | STARTUP-CHECK                  | `Agent(identity, runtime).run("hello")` returns an `AgentResponse` with non-empty content (using Mock provider).            | yes       |
| 8  | HEALTH-CHECK                   | `runtime.health()` returns a dict with a `status` key.                                                                     | yes       |
| 9  | READINESS-CHECK                | `runtime.readiness() == True`.                                                                                             | yes       |
| 10 | GOLDEN-FLOWS-CHECK             | Imports `tests/golden/test_golden_flows.py` and runs every `test_golden_*` via `asyncio.run`. Currently 13/13 pass.       | yes       |
| 11 | API-CHECK                      | If `fastapi` is installed: `create_app(runtime)` + `TestClient.get("/api/v1/health")` returns 200 + `status` in body. Else SKIP (optional). | no (when fastapi missing) |
| 12 | CLI-CHECK                      | `albra health` returns exit code 0.                                                                                        | yes       |
| 13 | MEMORY-CHECK                   | `MemoryManager.write()` + `recall()` round-trip; verifies isolation (agent A's memory is invisible to agent B).          | yes       |
| 14 | AUDIT-CHECK                    | `TamperEvidentAuditLog.append()` ×3 → `verify_chain().valid == True`; mutate entry → `valid == False` (tamper detected).  | yes       |
| 15 | FIREWALL-CHECK                 | `ActionFirewall()` with NO rules → `check("totally_unknown_action")` returns `decision == deny`, `reason == "no_matching_rule"` (DEFAULT-DENY). | yes       |
| 16 | DOCTOR-CHECK                   | `Doctor().diagnose()` returns a `DiagnosticReport` containing at least `runtime`, `memory`, `providers`, `tools`, `audit`, `security` components. | yes       |
| R  | REPRODUCIBILITY-NOTES          | INFORMATIONAL (always passes). Prints the scope: what the script verifies vs what would need external CI. See §10.          | yes (informational) |

### 3.1 Optional vs required vs informational

- **Required PASS/FAIL checks (1–16)** MUST pass. Failure → `CLEAN-ROOM: FAIL` and exit 1.
- **Optional checks (currently #2b and #17-A2A-CHECK)** may SKIP without
  failing the overall report. (#11 API-CHECK is also optional when
  `fastapi` is not installed; when `fastapi` IS installed it becomes
  required.)
- **Informational check R (REPRODUCIBILITY-NOTES)** is `required=True`
  but always passes — it is documentation printed inline in the report
  so anyone reading it can see at a glance what is and is not verified.
  It is NOT a gate.

---

## 4. Interpreting PASS / FAIL / SKIP

Each check prints a tag and details:

### 4.1 PASS

```
  [PASS]   13. MEMORY-CHECK (write+read+isolation)
           A_recalled=1 B_recalled=1 leaks=0
```

The check ran successfully and asserted the expected condition.

### 4.2 FAIL

```
  [FAIL]   14. AUDIT-CHECK (append + verify + tamper detected)
           before.valid=True len=3; after.valid=True kind=None
```

The check ran but its assertion failed. The `details` line shows what
happened. In this hypothetical example, mutating an entry did NOT invalidate
the chain — a serious regression.

### 4.3 SKIP

```
  [SKIP]   11. API-CHECK (fastapi NOT_INSTALLED — skip) (optional)
           fastapi is an optional dependency
```

The check was not run because a precondition (an optional dependency) was
missing. SKIP on an optional check does not affect the overall PASS.

---

## 5. The Final Summary

```
========================================================================
SUMMARY
========================================================================
#   CHECK                                                        RESULT
------------------------------------------------------------------------
1   1. CLONE-LIKE (fresh interpreter, sanitized env)              PASS
2   2. INSTALL-LIKE (required deps)                              PASS
3   2b. INSTALL-LIKE (optional deps)                             PASS
4   3. CONFIG-FROM-SCRATCH (from_env → CORE_ONLY)                PASS
5   4. INIT-FROM-SCRATCH (startup + readiness=True)              PASS
6   5. TEST-RUN (pytest: all tests passed, 0 failed)              PASS
7   6. BUILD-CHECK (import albra_seed; __seed_version__)         PASS
8   7. STARTUP-CHECK (Agent.run('hello'))                        PASS
9   8. HEALTH-CHECK (runtime.health() has 'status')              PASS
10  9. READINESS-CHECK (readiness()=True)                        PASS
11  10. GOLDEN-FLOWS-CHECK (13/13 passed)                        PASS
12  11. API-CHECK (FastAPI create_app + GET /api/v1/health)      PASS
13  12. CLI-CHECK (albra health → exit 0)                        PASS
14  13. MEMORY-CHECK (write+read+isolation)                      PASS
15  14. AUDIT-CHECK (append + verify + tamper detected)          PASS
16  15. FIREWALL-CHECK (default-deny)                            PASS
17  16. DOCTOR-CHECK (Doctor.diagnose → DiagnosticReport)        PASS
18  17. A2A-CHECK (AgentCard derived from AgentManifest)         PASS
19  R. REPRODUCIBILITY-NOTES (informational scope doc)           PASS
------------------------------------------------------------------------
required: 17/17 passed; optional: 2/2 passed

CLEAN-ROOM: PASS
```

The script exits 0 on `CLEAN-ROOM: PASS`; exit 1 on FAIL. The 17 required
breakdown is: 16 real PASS/FAIL checks (#1–#16) + 1 informational
scope-doc check (R, always passes). The 2 optional are 2b INSTALL-LIKE
optional deps and 17 A2A-CHECK.

---

## 6. How to Re-Run After Specialization

A specialization layer MUST NOT modify any file under `src/albra_seed/`.
Once specialization is in place under `src/albra_specialization/`, re-run:

```bash
python scripts/clean_room_verify.py
```

### 6.1 What the script does NOT test

The clean-room script verifies the SEED, not the specialization. After
specialization, the specialization author SHOULD add tests under
`tests/specialization/` covering:

- Identity tests (verify specialization metadata).
- Tool / Skill contract tests (every domain tool / skill has valid
  `ToolContract` / `SkillContract`).
- Workflow tests (domain workflows execute correctly).
- Integration tests (specialization + seed run end-to-end).
- Security tests (HITL triggers, firewall blocks, audit captures).
- Clean-room test (the seed still passes `scripts/clean_room_verify.py`
  after specialization).

### 6.2 Common reasons specialization breaks clean-room

| Symptom                                                | Likely cause                                                                |
| ------------------------------------------------------ | --------------------------------------------------------------------------- |
| CLONE-LIKE fails                                        | Specialization imported at module load time; not lazy.                       |
| INIT-FROM-SCRATCH fails                                 | Specialization requires an external provider as a hard dep.                  |
| FIREWALL-CHECK fails                                    | Specialization registered a wildcard ALLOW rule.                            |
| AUDIT-CHECK fails                                       | Specialization replaced the TamperEvidentAuditLog or mutated entries.       |
| DOCTOR-CHECK fails                                      | Specialization masked a component as HEALTHY+IMPLEMENTED without probing.  |
| GOLDEN-FLOWS-CHECK fails                                | Specialization broke one of the 13 universal flows.                          |

### 6.3 Recovery

For each failing check, see the corresponding test file (the script's
`details` line points at it). Fix the specialization — DO NOT modify the
seed. Once clean-room passes again, the specialization is integration-ready.

---

## 7. Clean-Room + Audit Matrix

`scripts/clean_room_verify.py` and `scripts/audit_matrix.py` are
complementary:

| Script                     | Question it answers                                          |
| -------------------------- | ------------------------------------------------------------- |
| `clean_room_verify.py`     | "Does the seed run end-to-end in CORE_ONLY mode?"              |
| `audit_matrix.py`          | "Is every component VERIFIED + IMPLEMENTED + TESTED?"          |

Run BOTH before declaring a clone ready. The audit matrix output is saved
to `docs/AUDIT_MATRIX.md` (auto-generated, do not edit by hand).

---

## 8. CI Integration

In CI, run (from a FRESH clone + FRESH venv — see §10.4):

```bash
python scripts/clean_room_verify.py
python scripts/audit_matrix.py > /dev/null   # writes docs/AUDIT_MATRIX.md
python scripts/parity_check.py                # exit 0, 106/106 DNA
python scripts/contamination_scan.py          # exit 0, 0 critical
pytest tests/ -q
```

The first command MUST exit 0 (17/17 required + 2/2 optional). The
second regenerates the audit matrix (ignored output, but the file is
committed). The third and fourth run the genetic gates. The fifth runs
the full test suite (test count computed dynamically at verification time
by `scripts/clean_room_verify.py::_count_tests()`).

NOTE: §10.2 lists what the script does NOT verify (fresh venv, fresh
clone, `pip install -e .` from scratch). The CI pipeline itself is
responsible for performing those steps BEFORE running
`clean_room_verify.py`; the script assumes the install has already
happened.

---

## 9. Final Status

The clean-room script can report `CLEAN-ROOM: PASS` (17/17 required = 16 PASS/FAIL
checks + 1 informational scope-doc check R; 2/2 optional). This is a code gate,
not a production sign-off. The canonical status in `docs/CERTIFICATION.md` is
`CODE GATES PASS — PRODUCTION NOT VERIFIED` until real-environment security/HITL
acceptance and the separate dashboard security checks have evidence.

---

## 10. Honest Scope — What This Script Verifies vs What It Does NOT

Check R (REPRODUCIBILITY-NOTES) prints this scope inline in the report.
It is reproduced here for offline reading.

### 10.1 What the script DOES verify (real checks, run in this process tree)

1. Package imports cleanly from a fresh subprocess with sanitized env
   (`ALBRA_*` and provider keys stripped).
2. Required dependencies (`pydantic`, `structlog`, `dotenv`) are importable
   in the current interpreter.
3. `SeedConfig.from_env()` works with no env vars → `CORE_ONLY` mode.
4. `Runtime.startup()` succeeds → `readiness()` returns `True`.
5. `pytest tests/` passes (all tests pass; exact count is recomputed dynamically by `scripts/clean_room_verify.py::_count_tests()`).
6. `import albra_seed; __seed_version__` returns `"ALBRA_SEED_V1.0"`.
7. `Agent.run("hello")` returns an `AgentResponse` with non-empty content
   (Mock provider — CORE_ONLY).
8. `runtime.health()` returns a dict containing the `status` key.
9. `runtime.readiness()` returns `True`.
10. All 13 golden flows pass.
11. FastAPI `create_app` + `TestClient.get("/api/v1/health")` returns 200
    with `status` in body (only when `fastapi` is installed).
12. CLI `albra health` returns exit code 0.
13. Memory write/read/isolation works (agent A's records are invisible
    to agent B; composite `agent_id + user_id + domain + context_id` key).
14. Audit log append + tamper-evident chain verification works
    (`verify_chain().valid == True`; mutate an entry → `valid == False`).
15. Firewall default-deny works (no rules → unknown action denied with
    `reason="no_matching_rule"`).
16. `Doctor.diagnose()` returns a `DiagnosticReport` with at least
    `runtime`, `memory`, `providers`, `tools`, `audit`, `security`.
17. (OPTIONAL) A2A `AgentCard` can be built from an `AgentManifest`.

### 10.2 What the script DOES NOT verify (would need external CI)

- **Fresh virtualenv creation** — assumes deps are already installed in
  the current interpreter (via `pip install -e .` or equivalent). The
  script does NOT create a venv and does NOT run `pip install`.
- **Clone from a git remote** — assumes you are already inside the
  checkout you want to verify. The script does NOT clone.
- **`pip install -e .` from scratch** — does NOT exercise the
  `pyproject.toml` install metadata, `entry_points`, `console_scripts`,
  or extras resolution.
- **Multiple Python version compatibility** — only the current
  interpreter is exercised.
- **Real network A2A interoperability** — the test suite uses
  `httpx.ASGITransport` (in-process, no real socket), not live HTTP to a
  remote agent on another host.
- **External provider integration** (OpenAI / Anthropic / Groq / Ollama
  / ZAI) — CORE_ONLY mode only. Optional providers are checked for
  import-ability but never invoked with real credentials.

### 10.3 External A2A interop — best-effort verification

The closest thing to real-network A2A interop that can be verified
without a live remote agent is **`tests/a2a/test_external_interop.py`**
(18 tests, all PASS). This file spins up a non-ALBRA FastAPI fixture
implementing the A2A 1.0 wire format and talks to it via
`httpx.ASGITransport` (in-process, no real network socket). The tests
cover:

- `discover` / `send` / `get_status` / `cancel` in both directions
  (ALBRA→Remote and Remote→ALBRA),
- ALBRA↔ALBRA end-to-end via HTTP,
- Security filter pipeline propagation in both directions,
- Wire format compatibility, malformed request handling, JSON-RPC
  error codes (−32601 for unknown methods),
- Artifact + correlation_id propagation,
- `HTTPTransport` raising clean `NotImplementedError` when `httpx` is
  missing (verified by patching `__import__`).

This is the BEST that can be done in a single-process verification run.
True cross-host interop would require a CI job that deploys a second
agent on a separate machine and exercises real network failure modes
(timeouts, partial reads, malformed frames).

### 10.4 How to perform a FULL clean-room verification (external CI)

The script alone is NOT a full clean-room verification — it is the
**second half** of one. The first half (fresh venv + fresh clone +
fresh install) must be done manually or in CI before running the script:

```bash
# 1. Clone the repo into a fresh directory (NOT your working copy)
git clone https://github.com/ALBRA8/AGENTE-GENERAL.git fresh-checkout
cd fresh-checkout

# 2. Create and activate a fresh virtualenv
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate          # Windows (PowerShell)

# 3. Install the package + dev extras (this is the part the script CANNOT
#    verify for itself — it assumes deps are already installed)
pip install -e .[dev]
# — or — pip install -e . && pip install pytest httpx fastapi

# 4. Run the clean-room verification
python scripts/clean_room_verify.py
#    → expected: required: 17/17 passed; optional: 2/2 passed
#    → expected final line: CLEAN-ROOM: PASS
#    → exit code MUST be 0

# 5. (Optional but recommended) Run the other gates too
python scripts/audit_matrix.py          # 40/40 VERIFIED
python scripts/parity_check.py          # exit 0, 106/106 DNA present
python scripts/contamination_scan.py    # exit 0, 0 critical
python -m pytest tests/ -q              # all tests pass (count recomputed dynamically)
```

If any of step 3 or step 4 fails on a fresh clone + fresh venv, the seed
is NOT clean-room ready. File the failure against the seed (NOT against
the specialization layer — the seed must clone-and-install cleanly with
no special hand-holding).
