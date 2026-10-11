# ALBRA AGENT SEED V1.0 — Clean-Room Verification Protocol

> La verificación clean-room garantiza que la semilla corre desde cero,
> sin cachés, sin credenciales externas, sin estado previo. Es el pasaporte
> de "INTEGRATION_READY" para los agentes especializados que clonen este repo.

This document explains what clean-room verification is, why it matters, the
16 checks performed by `scripts/clean_room_verify.py`, how to interpret the
PASS/FAIL report, and how to re-run it after specialization.

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
- Runs each of the 16 checks, printing `[PASS]` / `[FAIL]` / `[SKIP]`
  for each.
- Exits 0 if ALL required checks pass; exits 1 otherwise.

Expected output (final lines):

```
required: 15/15 passed; optional: 1/1 passed

CLEAN-ROOM: PASS
```

---

## 3. The 16 Checks

| #  | Name                          | What it verifies                                                                                                          | Required? |
| -- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------- | --------- |
| 1  | CLONE-LIKE                    | Fresh interpreter with `PATH` + `PYTHONPATH` only; `import albra_seed` succeeds and reports `__seed_version__`.           | yes       |
| 2  | INSTALL-LIKE (required deps)  | `pydantic`, `structlog`, `dotenv` import cleanly.                                                                          | yes       |
| 2b | INSTALL-LIKE (optional deps)  | `openai` / `anthropic` / `fastapi` / `uvicorn` / `httpx` reported as `OK` or `NOT_INSTALLED` without failing.             | no        |
| 3  | CONFIG-FROM-SCRATCH           | `SeedConfig.from_env()` in a subprocess with `ALBRA_*` and provider env vars stripped returns `is_core_only() == True`.    | yes       |
| 4  | INIT-FROM-SCRATCH              | `Runtime.startup()` + register `ProviderRouter(MockProvider)` → `readiness() == True`; then `shutdown()` cleanly.         | yes       |
| 5  | TEST-RUN                       | `pytest tests` runs cleanly: `N passed, 0 failed`. (Counts the entire suite — currently 280 tests.)                       | yes       |
| 6  | BUILD-CHECK                    | `python -c "import albra_seed; print(__seed_version__)"` returns `"ALBRA_SEED_V1.0"`.                                       | yes       |
| 7  | STARTUP-CHECK                  | `Agent(identity, runtime).run("hello")` returns an `AgentResponse` with non-empty content (using Mock provider).            | yes       |
| 8  | HEALTH-CHECK                   | `runtime.health()` returns a dict with a `status` key.                                                                     | yes       |
| 9  | READINESS-CHECK                | `runtime.readiness() == True`.                                                                                             | yes       |
| 10 | GOLDEN-FLOWS-CHECK             | Imports `tests/golden/test_golden_flows.py` and runs every `test_golden_*` via `asyncio.run`. Currently 13/13 pass.       | yes       |
| 11 | API-CHECK                      | If `fastapi` is installed: `create_app(runtime)` + `TestClient.get("/api/v1/health")` returns 200 + `status` in body. Else SKIP (optional). | no        |
| 12 | CLI-CHECK                      | `albra health` returns exit code 0.                                                                                        | yes       |
| 13 | MEMORY-CHECK                   | `MemoryManager.write()` + `recall()` round-trip; verifies isolation (agent A's memory is invisible to agent B).          | yes       |
| 14 | AUDIT-CHECK                    | `TamperEvidentAuditLog.append()` ×3 → `verify_chain().valid == True`; mutate entry → `valid == False` (tamper detected).  | yes       |
| 15 | FIREWALL-CHECK                 | `ActionFirewall()` with NO rules → `check("totally_unknown_action")` returns `decision == deny`, `reason == "no_matching_rule"` (DEFAULT-DENY). | yes       |
| 16 | DOCTOR-CHECK                   | `Doctor().diagnose()` returns a `DiagnosticReport` containing at least `runtime`, `memory`, `providers`, `tools`, `audit`, `security` components. | yes       |

### 3.1 Optional vs required

- **Required** checks MUST pass. Failure → `CLEAN-ROOM: FAIL` and exit 1.
- **Optional** checks (currently #2b and #11) may SKIP without failing
  the overall report.

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
6   5. TEST-RUN (pytest: 280 passed, 0 failed)                   PASS
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
------------------------------------------------------------------------
required: 15/15 passed; optional: 1/1 passed

CLEAN-ROOM: PASS
```

The script exits 0 on `CLEAN-ROOM: PASS`; exit 1 on FAIL.

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

In CI, run:

```bash
python scripts/clean_room_verify.py
python scripts/audit_matrix.py > /dev/null   # writes docs/AUDIT_MATRIX.md
pytest tests/ -q
```

The first command MUST exit 0. The second regenerates the audit matrix
(ignored output, but the file is committed). The third runs the full
test suite (280 tests as of this release).

---

## 9. Final Status

The seed is shipped with `CLEAN-ROOM: PASS` (16/16 checks). The
`INTEGRATION_READY` status in `docs/FINAL_REPORT.md` depends on this.
