# Staging Readiness Evaluation

**Status:** staging environment NOT yet provisioned. This document is
an evaluation of repository readiness and a list of external blockers.
It is NOT evidence that a staging environment has been deployed or tested.

**Branch:** `ops/staging-readiness` (based on `main` @ `24ab004c`)
**Date:** 2026-10-14 (UTC)
**Author:** ops engineer (this branch only — no `src/`, `tests/`, or
workflow changes)

---

## 1. Scope

This document evaluates whether the ALBRA SEED repository is ready to
be deployed to a staging environment. It covers:

- Repository capabilities that exist today (storage, API, health).
- Operational prerequisites that must be provisioned externally.
- Blockers that prevent a safe staging deployment.
- A readiness matrix with objective criteria.

It does NOT cover:
- Application code, tests, or CI (owned by the other engineer).
- Production deployment (out of scope until staging is accepted).
- HITL implementation details (see
  `docs/HITL_DURABLE_STORAGE_DESIGN.md` and
  `docs/HITL_PRODUCTION_OPERATIONS.md`).

---

## 2. Repository capabilities confirmed (2026-10-14)

The following were verified by running commands on this branch:

| Capability | Evidence | Status |
|---|---|---|
| Python 3.10+ supported | `pyproject.toml`: `requires-python = ">=3.10"` | ✅ |
| Clean install from source | `pip install -e '.[dev,a2a,api]'` succeeded on Python 3.12.15 | ✅ |
| Clean-room verification | `python scripts/clean_room_verify.py` → 17/17 required + 2/2 optional PASS | ✅ |
| Audit matrix | `python scripts/audit_matrix.py` → 40/40 VERIFIED | ✅ |
| Genetic parity | `python scripts/parity_check.py` → `is_pure_seed=True`, 0 failures | ✅ |
| Genetic contamination | `python scripts/contamination_scan.py` → 0 critical findings | ✅ |
| Test suite (no LLM) | `pytest tests/ --ignore=tests/llm_real` → 648 passed, 1 warning | ✅ |
| FastAPI app factory | `src/albra_seed/api/app.py::create_app()` exists | ✅ |
| Health endpoint | `GET /api/v1/health` (returns status, phase, components, sessions, uptime) | ✅ |
| Readiness endpoint | `GET /api/v1/readiness` (returns `ready: bool, phase: str`) | ✅ |
| Agent info endpoint | `GET /api/v1/agent/info` (returns identity, capabilities, autonomy) | ✅ |
| Execution trace endpoint | `GET /api/v1/execution/{id}` (returns cached trace) | ✅ |
| HITL approval API | `GET /api/v1/approvals/pending`, `POST /{id}/decision`, `POST /{id}/resume` | ✅ (per `HITL_DURABLE_STORAGE_DESIGN.md`) |
| SQLite approval store | `src/albra_seed/approvals/sqlite_store.py` — mode 0600, rejects symlinks | ✅ |
| SQLite dispatch ledger | `src/albra_seed/approvals/suspended_sqlite_store.py` — one-shot claims | ✅ |
| Config from env | `SeedConfig.from_env()` reads `ALBRA_*` env vars + standard provider keys | ✅ |
| Structured logging | `structlog` (JSON format default) | ✅ |
| CI workflow | `.github/workflows/clean-room.yml` (Python 3.10/3.11/3.12 matrix, `permissions: contents: read`) | ✅ |

## 3. Backup/restore drill (executed 2026-10-14)

A safe, self-contained backup/restore drill was executed using a test
SQLite database (NOT a production database). Result: **PASS**.

```
python3 scripts/ops_backup_drill.py
```

Summary (full output in `download/ops_backup_drill_output.txt`):

| Step | Result |
|---|---|
| Create test DB (1 approval, 1 dispatch, schema v1) | ✅ |
| Online backup via `sqlite3.Connection.backup()` (WAL-safe) | ✅ 0.3 ms, 20480 bytes |
| Backup integrity check | ✅ `integrity_check=ok`, schema v1, row counts match |
| Restore to isolated path (mode 0600) | ✅ |
| Restored DB integrity + row counts + sample row | ✅ |
| Negative test: corrupt DB header → `DatabaseError` | ✅ fail-closed |
| Negative test: random bytes (wrong decryption key) → `DatabaseError` | ✅ fail-closed |

**Limitations of this drill:**
- Used a test DB, not a real production DB with real approval payloads.
- Encryption was NOT tested (requires KMS / envelope-encryption setup).
- Concurrent-write scenario was NOT tested (requires a running worker).
- No real external provider was contacted.

---

## 4. External prerequisites (must be provisioned before staging)

These are NOT in the repository and cannot be provisioned by this branch.
Each requires a human with infrastructure access.

### 4.1 Compute and storage

| Prerequisite | Why | Owner |
|---|---|---|
| Staging host (Linux, x86_64 or arm64) | Run the API + workers | infra team |
| Encrypted persistent volume (host full-disk encryption or cloud block-storage encryption) | SQLite approval DB + WAL/SHM sidecars must be at rest encrypted | infra team |
| Backup target (object storage or independent volume) | Encrypted backups must leave the host | infra team |
| KMS or envelope-encryption key | Backup encryption key, separate from host disk-encryption key | security team |

### 4.2 Identity and secrets

| Prerequisite | Why | Owner |
|---|---|---|
| Real Authenticator (not NoopAuthenticator) | Approval API rejects anonymous by default; production cannot use mock auth | security team |
| Identity provider / token issuer | Short-lived tokens, validated issuer/audience/expiry/signature | security team |
| Service identity for runtime | Distinct from approval operators; least privilege | security team |
| Provider API keys (OpenAI / Anthropic / Groq / Z.AI / Ollama) | Only if staging should call real LLMs — otherwise use MockProvider | product owner |
| Secret manager / workload identity | Inject secrets at runtime; NEVER store in Git, images, env files, or logs | infra + security |

### 4.3 Network

| Prerequisite | Why | Owner |
|---|---|---|
| TLS-terminating reverse proxy (nginx, Caddy, ALB) | Never expose Uvicorn directly to the internet | infra team |
| Egress firewall (allowlist model/provider endpoints + IdP) | Deny arbitrary egress by default | infra team |
| Security groups / network ACLs | Restrict SQLite to the trusted host; never expose DB port | infra team |

### 4.4 Observability

| Prerequisite | Why | Owner |
|---|---|---|
| Log aggregation (JSON structured) | `structlog` emits JSON; ship to Loki/CloudWatch/Datadog | infra team |
| Metrics endpoint or OTel collector | `ALBRA_METRICS_ENABLED=true`, `ALBRA_OTEL_ENDPOINT` (optional) | infra team |
| Alerting on: DB errors, stale pending, uncertain dispatches, disk full, backup age | See `INCIDENT_RESPONSE.md` | ops team |

---

## 5. Readiness matrix

Each criterion has an objective pass/fail condition. Status is honest —
`BLOCKED` means an external prerequisite is missing; `NOT RUN` means
the check has not been executed against a real staging environment.

| # | Criterion | Objective condition | Status | Evidence |
|---|---|---|---|---|
| 1 | Clean install from source | `pip install -e '.[dev,a2a,api]'` succeeds | ✅ PASS | drill 2026-10-14, Python 3.12.15 |
| 2 | Clean-room gates pass | 17/17 required + 2/2 optional | ✅ PASS | drill 2026-10-14 |
| 3 | Test suite passes (no LLM) | `pytest tests/ --ignore=tests/llm_real` → all green | ✅ PASS | 648 passed |
| 4 | CI green on Python 3.10/3.11/3.12 | GitHub Actions success on `main` HEAD | ✅ PASS | `main@24ab004c` (per CI history) |
| 5 | API starts and responds | `GET /api/v1/health` returns 200 | ❌ BLOCKED | no staging host provisioned |
| 6 | Real authenticator configured | Not `NoopAuthenticator`; approval API rejects anonymous | ❌ BLOCKED | requires IdP + secret manager |
| 7 | Policy rules for approval:list/decide/resume | Narrowly scoped; deny by default | ❌ BLOCKED | requires policy deployment |
| 8 | Encrypted persistent volume | `ALBRA_APPROVAL_DB_PATH` on encrypted volume; WAL/SHM sidecars too | ❌ BLOCKED | requires infra provisioning |
| 9 | Backup encryption configured | Envelope encryption with separate key; backup target cannot decrypt | ❌ BLOCKED | requires KMS |
| 10 | Backup/restore drill on real DB | Restore to isolated path; integrity + row counts verified | ⚠️ PARTIAL | drill on TEST DB passed; real-DB drill NOT RUN |
| 11 | TLS termination at ingress | HTTPS only; Uvicorn not directly exposed | ❌ BLOCKED | requires reverse proxy |
| 12 | Egress firewall (allowlist) | Only model/provider endpoints + IdP + telemetry | ❌ BLOCKED | requires network config |
| 13 | Log aggregation + alerting | JSON logs shipped; alerts on DB errors, stale pending, uncertain | ❌ BLOCKED | requires log infra |
| 14 | Staging isolated from production | Separate DB, separate secrets, separate network | ❌ BLOCKED | requires environment separation |
| 15 | Acceptance tests executed in staging | Per `ACCEPTANCE_EVIDENCE.md` matrix | ❌ NOT RUN | staging not deployed |

---

## 6. Blockers preventing staging acceptance

### P1 — Deployment blockers (must resolve before staging)

| ID | Blocker | Resolution | Owner |
|---|---|---|---|
| B-01 | No staging host provisioned | Provision Linux host with encrypted persistent volume | infra team |
| B-02 | No real Authenticator configured | Deploy IdP, register real Authenticator (not Noop) | security team |
| B-03 | No policy rules for approval permissions | Deploy narrowly scoped `approval:list`, `approval:decide`, `approval:resume` rules | security team |
| B-04 | No backup encryption / KMS | Provision KMS, configure envelope encryption for backups | security team |
| B-05 | No TLS-terminating reverse proxy | Deploy nginx/Caddy/ALB in front of Uvicorn | infra team |
| B-06 | No egress firewall | Configure allowlist egress (model providers + IdP + telemetry only) | infra team |
| B-07 | No log aggregation / alerting | Ship JSON logs to aggregator; configure alerts per `INCIDENT_RESPONSE.md` | ops team |

### P2 — Important risks (resolve before production, not staging)

| ID | Risk | Mitigation | Owner |
|---|---|---|---|
| R-01 | SQLite single-writer concurrency | One DB shared by all workers on same host; do NOT use network FS | infra team |
| R-02 | `Agent.run()` coroutine not resumable after crash | Explicit `/resume` endpoint for approved calls only; operator must invoke manually | other engineer (code) |
| R-03 | External side-effect atomicity gap | Use provider idempotency keys; reconcile uncertain dispatches manually | ops team |
| R-04 | No automated retention/purge job | Manual purge after product/legal sign-off; see `BACKUP_RESTORE_RUNBOOK.md` § retention | product + legal |

### P3 — Operational improvements (non-blocking)

| ID | Improvement | Benefit | Owner |
|---|---|---|---|
| I-01 | Add `ALBRA_APPROVAL_DB_PATH` to a deployment .env.example | Reduces operator error | other engineer (config) |
| I-02 | Add `/api/v1/metrics` endpoint (Prometheus format) | Native metrics without OTel collector | other engineer (code) |
| I-03 | Add `/api/v1/approvals/stats` endpoint (counts by status, no payloads) | Easier monitoring of stale pending | other engineer (code) |

---

## 7. What this branch did NOT do

To respect the isolation rules (§2 of the mission brief):

- **No changes to `src/`, `tests/`, `.github/workflows/`, `pyproject.toml`**.
- **No changes to the HITL engine, SQLite coordinator, `Agent.run()`, or tool adapters.**
- **No changes to the other engineer's branches** (`fix/hitl-deadline-guard`, PR #3).
- **No deployment to any environment** (no staging host exists).
- **No real backup of any production database** (drill used a test DB).
- **No real provider was called** (drill was self-contained).

The only new files are in `docs/ops/` and one helper script
`scripts/ops_backup_drill.py` (read-only, does not modify any
application code).

---

## 8. Next steps for the responsible operator

1. **Provision infrastructure** per §4 (host, encrypted volume, KMS, IdP, reverse proxy, egress firewall, log aggregation).
2. **Deploy the SEED** to the staging host using the commands in `DEPLOYMENT_CHECKLIST.md`.
3. **Run the acceptance tests** in `ACCEPTANCE_EVIDENCE.md` and record results.
4. **Execute a real backup/restore drill** against the staging DB per `BACKUP_RESTORE_RUNBOOK.md`.
5. **Sign off** staging acceptance only when every applicable criterion in §5 is `PASS`.

Staging is NOT ready until every `BLOCKED` item above is resolved and
every `NOT RUN` acceptance test is executed with evidence.
