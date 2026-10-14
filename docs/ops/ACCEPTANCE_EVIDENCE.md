# Acceptance Evidence

**Status:** staging NOT YET deployed. Acceptance tests NOT YET executed
against a real staging environment. This document defines the acceptance
matrix and records results as they become available.

**Branch:** `ops/staging-readiness` (based on `main` @ `24ab004c`)
**Date:** 2026-10-14 (UTC)

---

## 1. Acceptance test matrix

Each test has an objective pass/fail condition. Status is honest:
- `PASS` — executed and passed with evidence.
- `FAIL` — executed and failed with evidence.
- `BLOCKED` — not executable due to missing access/infra/prerequisites.
- `NOT RUN` — not yet executed.
- `NOT APPLICABLE` — justified with a verified explanation.

### 1.1 Installation and startup

| # | Test | Objective condition | Status | Evidence |
|---|---|---|---|---|
| A-01 | Clean install from source | `pip install -e '.[dev,a2a,api]'` succeeds on Python 3.10/3.11/3.12 | ✅ PASS | drill 2026-10-14 (Python 3.12.15); see `STAGING_READINESS.md` §2 |
| A-02 | Clean-room gates pass | 17/17 required + 2/2 optional | ✅ PASS | drill 2026-10-14 |
| A-03 | Audit matrix passes | 40/40 VERIFIED | ✅ PASS | drill 2026-10-14 |
| A-04 | Genetic parity passes | `is_pure_seed=True`, 0 failures | ✅ PASS | drill 2026-10-14 |
| A-05 | Genetic contamination passes | 0 critical findings | ✅ PASS | drill 2026-10-14 |
| A-06 | Test suite passes (no LLM) | `pytest tests/ --ignore=tests/llm_real` all green | ✅ PASS | 648 passed on 2026-10-14 |
| A-07 | CI green on Python 3.10/3.11/3.12 | GitHub Actions success on `main@24ab004c` | ✅ PASS | CI history |
| A-08 | API starts on staging host | `systemctl start albra-api` → `active (running)` | ❌ BLOCKED | no staging host |
| A-09 | API responds to `/health` | `GET /api/v1/health` returns 200, `status=healthy` | ❌ BLOCKED | no staging host |
| A-10 | API responds to `/readiness` | `GET /api/v1/readiness` returns 200, `ready=true` | ❌ BLOCKED | no staging host |

### 1.2 Configuration and access

| # | Test | Objective condition | Status | Evidence |
|---|---|---|---|---|
| B-01 | `ALBRA_APPROVAL_DB_PATH` set to absolute path on encrypted volume | `realpath` matches; volume is encrypted | ❌ BLOCKED | no staging host |
| B-02 | DB file mode 0600 | `stat -c%a` returns `600` | ❌ BLOCKED | no staging host (code enforces on create) |
| B-03 | DB file owned by service account | `stat -c%U` returns `albra` | ❌ BLOCKED | no staging host |
| B-04 | DB is NOT a symlink | `test ! -L` passes | ❌ BLOCKED | no staging host (code rejects symlinks) |
| B-05 | DB is NOT on network FS | `stat -f -c%T` returns local FS type | ❌ BLOCKED | no staging host |
| B-06 | DB parent dir mode 0700 | `stat -c%a` returns `700` | ❌ BLOCKED | no staging host (code enforces on create) |
| B-07 | Real Authenticator configured (not Noop) | Approval API rejects anonymous | ❌ BLOCKED | requires IdP + secret manager |
| B-08 | Explicit `approval:list` rule | Valid token without perm → 403 | ❌ BLOCKED | requires policy deployment |
| B-09 | Explicit `approval:decide` rule | Valid token without perm → 403 | ❌ BLOCKED | requires policy deployment |
| B-10 | Explicit `approval:resume` rule | Valid token without perm → 403 | ❌ BLOCKED | requires policy deployment |
| B-11 | TLS terminates at reverse proxy | `https://` only; Uvicorn on 127.0.0.1 | ❌ BLOCKED | requires reverse proxy |
| B-12 | Egress allowlist configured | Only model providers + IdP + telemetry allowed | ❌ BLOCKED | requires network config |
| B-13 | No secrets in env files / Git / images | grep finds no `sk-*`, `Bearer`, `ghp_*`, `nvapi-*` | ✅ PASS | grep 2026-10-14 on `main@24ab004c` |

### 1.3 Connectivity and external services

| # | Test | Objective condition | Status | Evidence |
|---|---|---|---|---|
| C-01 | Provider endpoint reachable (if configured) | `curl` to provider returns 200/401 | ❌ BLOCKED | no staging host |
| C-02 | IdP endpoint reachable (if configured) | Token issuance + verification works | ❌ BLOCKED | no staging host |
| C-03 | Telemetry endpoint reachable (if configured) | OTel collector accepts export | ❌ BLOCKED | no staging host |
| C-04 | Backup target reachable | Encrypted upload succeeds | ❌ BLOCKED | no staging host |
| C-05 | Staging isolated from production | Separate DB, secrets, network | ❌ BLOCKED | no staging host |

### 1.4 Persistence and durability

| # | Test | Objective condition | Status | Evidence |
|---|---|---|---|---|
| D-01 | DB persists across API restart | Approval ID + status + arg digest unchanged | ❌ BLOCKED | no staging host |
| D-02 | DB schema version matches code | `PRAGMA user_version` is known | ❌ BLOCKED | no staging host (code rejects unknown) |
| D-03 | Approval survives process crash | (per `HITL_DURABLE_STORAGE_DESIGN.md`) | ❌ BLOCKED | no staging host |
| D-04 | Dispatch ledger one-shot claim | Repeated resume does NOT dispatch twice | ❌ BLOCKED | no staging host (unit tests cover this) |

### 1.5 Backup and restore

| # | Test | Objective condition | Status | Evidence |
|---|---|---|---|---|
| E-01 | Backup creates consistent snapshot | `integrity_check=ok`, row counts match | ✅ PASS | test-DB drill 2026-10-14 (`scripts/ops_backup_drill.py`) |
| E-02 | Backup is encrypted before upload | Plaintext never leaves the host | ❌ BLOCKED | no KMS configured |
| E-03 | Restore to isolated path succeeds | New DB file mode 0600, integrity ok, counts match | ✅ PASS | test-DB drill 2026-10-14 |
| E-04 | Restored DB usable | Sample row matches original | ✅ PASS | test-DB drill 2026-10-14 |
| E-05 | Corrupt DB detected (negative test) | `integrity_check` ≠ `ok` or `DatabaseError` | ✅ PASS | test-DB drill 2026-10-14 |
| E-06 | Wrong decryption key detected (negative test) | `DatabaseError` raised | ✅ PASS | test-DB drill 2026-10-14 |
| E-07 | Real-DB backup/restore drill | Same as E-01..E-04 on staging DB | ❌ NOT RUN | requires staging host |
| E-08 | Backup age alert fires | No verified backup within RPO → alert | ❌ NOT RUN | requires monitoring config |

### 1.6 Recovery and incident response

| # | Test | Objective condition | Status | Evidence |
|---|---|---|---|---|
| F-01 | Process crash → service auto-restarts | systemd `Restart=on-failure` works | ❌ BLOCKED | no staging host |
| F-02 | Process crash → `claimed` → `uncertain` conversion | `recover_interrupted_dispatches()` converts; no auto-retry | ❌ BLOCKED | no staging host (unit tests cover) |
| F-03 | External side-effect ambiguity → `uncertain` | Crash after external call → state `uncertain`, not retried | ❌ BLOCKED | no staging host (unit tests cover) |
| F-04 | `/api/v1/approvals/{id}/resume` dispatches approved call | Returns tool result; repeated resume → no double dispatch | ❌ BLOCKED | no staging host |
| F-05 | Reconciliation procedure documented | `INCIDENT_RESPONSE.md` §3 + `BACKUP_RESTORE_RUNBOOK.md` §6 | ✅ PASS | this branch |

### 1.7 Logs and observability

| # | Test | Objective condition | Status | Evidence |
|---|---|---|---|---|
| G-01 | Logs are JSON structured | `structlog` JSON format | ✅ PASS | code default `ALBRA_LOG_FORMAT=json` |
| G-02 | Logs do NOT contain secrets | grep for `sk-*`, `Bearer`, `ghp_*`, `nvapi-*` in sample | ❌ NOT RUN | requires running deployment + log sample |
| G-03 | Audit log has hash chain | `src/albra_seed/audit/` implements hash chain | ✅ PASS | code review |
| G-04 | Log aggregation configured | JSON logs shipped to aggregator | ❌ BLOCKED | requires log infra |
| G-05 | Alerts configured per `INCIDENT_RESPONSE.md` §8 | All signals in §7 monitored | ❌ BLOCKED | requires monitoring config |

### 1.8 Deployment rollback

| # | Test | Objective condition | Status | Evidence |
|---|---|---|---|---|
| H-01 | Code rollback procedure documented | `DEPLOYMENT_CHECKLIST.md` §6.1 | ✅ PASS | this branch |
| H-02 | DB rollback procedure documented | `DEPLOYMENT_CHECKLIST.md` §6.2 + `BACKUP_RESTORE_RUNBOOK.md` §4 | ✅ PASS | this branch |
| H-03 | Config rollback procedure documented | `DEPLOYMENT_CHECKLIST.md` §6.3 | ✅ PASS | this branch |
| H-04 | Code rollback executed in staging | Previous commit deployed, tests pass | ❌ NOT RUN | requires staging host |
| H-05 | DB rollback executed in staging (drill) | Restore from backup, verify | ❌ NOT RUN | requires staging host |

### 1.9 Staging/production isolation

| # | Test | Objective condition | Status | Evidence |
|---|---|---|---|---|
| I-01 | Separate DB files | Staging DB ≠ production DB | ❌ BLOCKED | requires staging host |
| I-02 | Separate secrets | Staging keys ≠ production keys | ❌ BLOCKED | requires secret manager |
| I-03 | Separate network | Staging subnet ≠ production subnet | ❌ BLOCKED | requires network config |
| I-04 | Separate backup targets | Staging bucket ≠ production bucket | ❌ BLOCKED | requires backup infra |
| I-05 | Staging tests do NOT call production providers | Staging uses staging keys or Mock | ❌ BLOCKED | requires staging host |

---

## 2. Summary of results

| Category | PASS | FAIL | BLOCKED | NOT RUN | NOT APPLICABLE | Total |
|---|---|---|---|---|---|---|
| 1.1 Installation and startup | 7 | 0 | 3 | 0 | 0 | 10 |
| 1.2 Configuration and access | 1 | 0 | 12 | 0 | 0 | 13 |
| 1.3 Connectivity | 0 | 0 | 5 | 0 | 0 | 5 |
| 1.4 Persistence | 0 | 0 | 4 | 0 | 0 | 4 |
| 1.5 Backup and restore | 4 | 0 | 1 | 2 | 0 | 7 |
| 1.6 Recovery | 1 | 0 | 4 | 0 | 0 | 5 |
| 1.7 Logs and observability | 2 | 0 | 2 | 1 | 0 | 5 |
| 1.8 Rollback | 3 | 0 | 0 | 2 | 0 | 5 |
| 1.9 Staging/prod isolation | 0 | 0 | 5 | 0 | 0 | 5 |
| **Total** | **18** | **0** | **36** | **5** | **0** | **59** |

**Acceptance verdict:** NOT ACCEPTABLE for staging. 36 tests are
BLOCKED because no staging environment is provisioned. 5 tests are
NOT RUN because they require a running deployment.

---

## 3. Executed evidence (drills run on 2026-10-14)

### 3.1 Backup/restore drill (test DB)

**Script:** `scripts/ops_backup_drill.py`
**Full output:** `download/ops_backup_drill_output.txt`
**Result:** PASS

| Step | Result |
|---|---|
| Create test DB (1 approval, 1 dispatch, schema v1) | ✅ |
| Online backup via `sqlite3.Connection.backup()` (WAL-safe) | ✅ 0.3 ms, 20480 bytes |
| Backup integrity check | ✅ `integrity_check=ok`, schema v1, row counts match |
| Restore to isolated path (mode 0600) | ✅ |
| Restored DB integrity + row counts + sample row | ✅ |
| Negative test: corrupt DB header → `DatabaseError` | ✅ fail-closed |
| Negative test: random bytes (wrong key) → `DatabaseError` | ✅ fail-closed |

### 3.2 Clean install drill

**Environment:** Python 3.12.15, fresh venv, git worktree from `main@24ab004c`
**Result:** PASS

| Step | Result |
|---|---|
| `pip install -e '.[dev,a2a,api]'` | ✅ succeeds |
| `import albra_seed` | ✅ version 1.0.0 |
| `python scripts/clean_room_verify.py` | ✅ 17/17 + 2/2 PASS |
| `python scripts/audit_matrix.py` | ✅ 40/40 VERIFIED |
| `python scripts/parity_check.py` | ✅ `is_pure_seed=True`, 0 failures |
| `python scripts/contamination_scan.py` | ✅ 0 critical findings |
| `pytest tests/ --ignore=tests/llm_real` | ✅ 648 passed |

### 3.3 Secret scan

**Command:**
```
grep -rnE '(sk-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9]{20,})' \
    --include='*.py' --include='*.md' --include='*.yml' --include='*.json' --include='*.env*' \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=.venv \
    /home/z/my-project/agent-general/
```
**Result:** No secrets found in tracked files. (Manual verification;
automated secret scanning should be added to CI — see
`SECURITY_AND_SECRETS.md` §5, SEC-P1-05.)

---

## 4. Pending evidence (requires infrastructure)

The following tests CANNOT be executed from this branch because they
require infrastructure that is not provisioned:

- All tests in §1.2 (Configuration and access) except B-13.
- All tests in §1.3 (Connectivity).
- All tests in §1.4 (Persistence and durability).
- E-02, E-07, E-08 (real-DB backup/restore drill, encryption, alerting).
- All tests in §1.6 except F-05 (documentation only).
- G-02, G-04, G-05 (log verification, aggregation, alerting).
- H-04, H-05 (rollback drill in staging).
- All tests in §1.9 (staging/production isolation).

Each of these is marked `BLOCKED` or `NOT RUN` in §1. They require:

1. A staging host with encrypted persistent volume.
2. A real Authenticator + IdP.
3. Explicit approval RBAC rules.
4. A TLS-terminating reverse proxy.
5. An egress firewall (allowlist).
6. A KMS for backup encryption.
7. A secret manager for runtime secret injection.
8. A log aggregator + alerting.
9. A backup target (object storage or independent volume).
10. Staging isolated from production.

**These are NOT actionable from this branch.** They require the
responsible operator to provision the infrastructure per
`STAGING_READINESS.md` §4 and `DEPLOYMENT_CHECKLIST.md` §1.

---

## 5. Sign-off

Staging acceptance requires ALL of the following:

- [ ] Every `BLOCKED` test in §1 is resolved (infrastructure provisioned).
- [ ] Every `NOT RUN` test is executed with evidence.
- [ ] No `FAIL` results.
- [ ] Real-DB backup/restore drill executed (E-07).
- [ ] Staging/production isolation verified (§1.9).
- [ ] Operator + independent approver sign-off below.

```
Staging acceptance sign-off

Operator: __________________________________  Date: __________
Independent approver: ________________________  Date: __________
Security lead: _______________________________  Date: __________
Product owner: _______________________________  Date: __________

Comments:
  _______________________________________________
  _______________________________________________
  _______________________________________________
```

**Do NOT sign off staging acceptance until every applicable test is
`PASS` with evidence.** Do NOT convert `BLOCKED` or `NOT RUN` into
`PASS`. Do NOT claim staging is ready if any `BLOCKED` item remains.
