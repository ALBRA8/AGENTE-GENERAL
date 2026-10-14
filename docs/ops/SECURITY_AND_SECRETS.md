# Security and Secrets Operational Review

**Status:** operational review of the repository's security posture
as observed from `main@24ab004c`. Does NOT include a penetration test,
a network scan, or an audit of a deployed environment.

**Scope:** secrets management, file permissions, network exposure,
identity, and operational risks visible from the repository.

**NOT in scope:** application code changes (owned by the other engineer),
CI workflow changes, dependency updates.

---

## 1. Secrets inventory

### 1.1 Secrets the deployment needs

| Secret | Purpose | Where it lives | Rotation |
|---|---|---|---|
| `OPENAI_API_KEY` | OpenAI provider (optional) | secret manager → env at runtime | per OpenAI policy |
| `ANTHROPIC_API_KEY` | Anthropic provider (optional) | secret manager → env at runtime | per Anthropic policy |
| `GROQ_API_KEY` | Groq provider (optional) | secret manager → env at runtime | per Groq policy |
| `ZAI_API_KEY` | Z.AI provider (optional) | secret manager → env at runtime | per Z.AI policy |
| `OLLAMA_BASE_URL` | Ollama endpoint (optional) | env or config | n/a |
| Authenticator signing key | Token verification | secret manager → injected | quarterly |
| Backup encryption key | Envelope encryption for backups | KMS / offline key custodian | annually + after incident |
| IdP client secret | Token issuance | IdP admin console | per IdP policy |
| `ALBRA_VAULT_TOKEN` | HashiCorp Vault (if used) | secret manager | per Vault policy |

### 1.2 Secrets the repository must NOT contain

The following must NEVER be in Git, env files committed to the repo,
container images, command-line arguments, application logs, or SQLite:

- Any `*_API_KEY` value.
- Any bearer token, session cookie, or OAuth refresh token.
- Any private key, TLS private key, or KMS key material.
- Any database password (SQLite uses file permissions, not passwords,
  but if a future backend requires a password, it must be in the secret
  manager).
- Any backup encryption key material.

### 1.3 Repository scan (executed 2026-10-14)

```
grep -rnE '(sk-[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|nvapi-[A-Za-z0-9]{20,})' \
    --include='*.py' --include='*.md' --include='*.yml' --include='*.json' --include='*.env*' \
    --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=.venv \
    /home/z/my-project/agent-general/
```

**Result:** No secrets found in tracked files. (Verified manually;
automated secret scanning should be added to CI — see §6.)

---

## 2. File permissions and placement

### 2.1 SQLite approval DB

| Aspect | Code behavior | Operator must verify |
|---|---|---|
| File mode | `SQLiteApprovalStore` creates with `0600` (verified in `src/albra_seed/approvals/sqlite_store.py:57,65`) | Pre-existing files; WAL/SHM sidecars; backup staging dirs |
| Symlink rejection | `SQLiteApprovalStore` rejects symlinked DB paths (verified at `sqlite_store.py:47-48`) | Operator must not point `ALBRA_APPROVAL_DB_PATH` at a symlink |
| Parent dir mode | Code creates parent dirs with `0700` | Pre-existing parent dirs; deployment volume mount options |
| Network FS | Code does NOT check for NFS; SQLite WAL locking is unreliable on NFS | Operator must ensure DB is on a local filesystem |

### 2.2 Logs

| Log file | Default path | Sensitive content risk |
|---|---|---|
| Agent log | `./logs/agent.log` (configurable via `ALBRA_LOG_PATH`) | May contain redacted but sensitive content; structlog emits JSON |
| Audit log | `./logs/audit.jsonl` (configurable via `ALBRA_AUDIT_LOG_PATH`) | Contains decision records, actor identities, action names; NEVER full arguments |

**Operator must:**
- Set log dir to `0750`, owned by `albra:albra`.
- Ship logs to an aggregator with TLS + access control.
- Verify the redactor (`src/albra_seed/memory/sanitizer.py`) is active
  (it is by default; `ALBRA_GUARDRAILS_ENABLED=true`).
- Review log samples for accidental secret leakage (especially during
  debugging — never log `os.environ` or full request bodies).

### 2.3 Config file

- `/etc/albra/albra.env` must be `0640`, owned by `root:albra`.
- Real secrets must be injected by the secret manager at runtime, NOT
  written to this file.
- The file must NOT be in Git, in a container image, or in a backup
  snapshot without encryption.

---

## 3. Network exposure

### 3.1 Ingress

| Port | Service | Exposure |
|---|---|---|
| 8000 | Uvicorn (ALBRA API) | `127.0.0.1` only — reverse proxy in front |
| 443 | nginx/Caddy/ALB | Public or internal-network only, TLS-terminated |

**Rule:** Uvicorn must NEVER be directly exposed to the internet.
TLS must terminate at the reverse proxy. The reverse proxy must:
- Allow only approved source ranges or identity-aware gateway.
- Set reasonable timeouts (300s for long-running requests).
- Forward `X-Forwarded-Proto: https`.
- Rate-limit and request-size-limit.

### 3.2 Egress

| Destination | Purpose | Required? |
|---|---|---|
| `api.openai.com:443` | OpenAI provider | only if `OPENAI_API_KEY` set |
| `api.anthropic.com:443` | Anthropic provider | only if `ANTHROPIC_API_KEY` set |
| `api.groq.com:443` | Groq provider | only if `GROQ_API_KEY` set |
| `api.z.ai:443` | Z.AI provider | only if `ZAI_API_KEY` set |
| `localhost:11434` | Ollama provider (local) | only if `OLLAMA_BASE_URL` set |
| IdP endpoint (e.g. `accounts.google.com`) | Token verification | if real Authenticator uses external IdP |
| Telemetry endpoint (OTel collector) | Metrics/traces export | only if `ALBRA_OTEL_ENDPOINT` set |
| Backup target (S3/GCS/Azure Blob) | Encrypted backup upload | for backup job only |

**Rule:** Deny all egress by default. Allowlist ONLY the destinations
above that are actually used. Document each exception and its owner.

### 3.3 SQLite

- SQLite DB file must be LOCAL to the trusted host.
- NEVER expose the DB port or file over the network.
- NEVER mount the DB directory over NFS or other network FS.

---

## 4. Identity and access management

### 4.1 Service accounts

| Account | Purpose | Permissions | Access to secrets? |
|---|---|---|---|
| `albra` (runtime) | Run the API + workers | Read DB, write DB, read provider keys from secret manager | Yes — provider keys, DB |
| `albra-backup` (backup job) | Create + upload encrypted backups | Read DB (read-only), write to backup target, use backup encryption key | Yes — backup encryption key |
| `albra-restore` (restore operator) | Decrypt + restore backups | Read from backup target, use decryption key, write to new DB path | Yes — decryption key |
| KMS admin | Manage encryption keys | KMS admin; NO application access | Yes — KMS master key |
| Approver (human) | Decide HITL approvals | `approval:list`, `approval:decide` only | No — should not have shell/DB access |
| Resume operator (human) | Resume approved calls | `approval:resume` only | No |

### 4.2 RBAC for HITL

| Permission | Who has it? | What it allows |
|---|---|---|
| `approval:list` | Approvers, ops | `GET /api/v1/approvals/pending` |
| `approval:decide` | Approvers only | `POST /api/v1/approvals/{id}/decision` |
| `approval:resume` | Resume operators only | `POST /api/v1/approvals/{id}/resume` |

**Rules:**
- A generic `ALLOW *` rule is NOT sufficient — the engine requires
  explicit, narrowly scoped rules for each permission.
- Approvers should NOT have `approval:resume` (separation of duties).
- Approvers should NOT have shell/root/DB-write access as part of their
  normal role.
- The default `NoopAuthenticator` is anonymous and CANNOT approve —
  a real Authenticator must be deployed.

### 4.3 Token handling

- Use short-lived tokens (15-min max) or workload identity.
- Validate issuer, audience, expiry, signature in the real Authenticator.
- NEVER put bearer tokens in URLs or logs.
- Rotate credentials per the IdP's policy.
- Revoke credentials immediately on suspected compromise.

---

## 5. Security control matrix

| Control | Evidence | Risk if missing | Status | Action needed | Owner |
|---|---|---|---|---|---|
| DB file mode 0600 | `sqlite_store.py:57,65` | Unauthorized read of approvals + tool args | ✅ Code enforces | Verify on deployed DB + WAL/SHM | ops |
| DB symlink rejection | `sqlite_store.py:47-48` | Path traversal / symlink attack | ✅ Code enforces | — | — |
| DB parent dir mode 0700 | `sqlite_store.py` | Unauthorized directory access | ✅ Code enforces (new dirs) | Verify pre-existing dirs | ops |
| Authenticator (not Noop) | `HITL_DURABLE_STORAGE_DESIGN.md` | Anonymous approval = security disaster | ❌ BLOCKED | Deploy real IdP + Authenticator | security |
| Explicit approval RBAC rules | `HITL_PRODUCTION_OPERATIONS.md` §7 | Over-broad privileges | ❌ BLOCKED | Deploy narrowly scoped rules | security |
| TLS at ingress | deployment config | Plaintext credentials / approvals | ❌ BLOCKED | Deploy reverse proxy with TLS | infra |
| Egress allowlist | deployment config | Data exfiltration, SSRF | ❌ BLOCKED | Configure egress firewall | infra |
| Encrypted volume | infra provisioning | Lost media = lost secrets + approvals | ❌ BLOCKED | Provision encrypted persistent volume | infra |
| Backup encryption | KMS + envelope | Lost backup = lost approvals | ❌ BLOCKED | Provision KMS + age recipient key | security |
| Secret manager | infra provisioning | Secrets in env files / Git / images | ❌ BLOCKED | Deploy Vault / cloud secret manager | infra |
| Log redaction | `sanitizer.py` + structlog | Secret leakage in logs | ✅ Code enforces | Verify log samples for leakage | ops |
| Audit log tamper-evidence | `src/albra_seed/audit/` | Audit log modification | ✅ Code has hash chain | Verify hash chain integrity | ops |
| CI secret scanning | `.github/workflows/clean-room.yml` | Secrets committed to Git | ❌ NOT CONFIGURED | Add `trufflehog` or `gitleaks` step | other engineer (CI) |
| Dependency vulnerability scan | `.github/workflows/clean-room.yml` | Known-vulnerable deps | ⚠️ Dependabot enabled (per commit `4c83885`) | Verify Dependabot alerts are reviewed | other engineer (CI) |
| Rate limiting | reverse proxy config | DoS / brute force | ❌ BLOCKED | Configure rate limit at reverse proxy | infra |
| Request size limit | reverse proxy config | DoS via large payloads | ❌ BLOCKED | Configure `client_max_body_size` | infra |
| Debug/docs endpoints not public | deployment config | Information disclosure | ❌ BLOCKED | Block `/docs`, `/redoc`, `/openapi.json` in prod | infra |

---

## 6. Findings (P0-P3)

### P0 — Critical (no findings)

None observed in the repository code. The risks above are all
"BLOCKED" because they require infrastructure provisioning, not code
changes.

### P1 — Deployment blockers

| ID | Finding | Evidence | Mitigation |
|---|---|---|---|
| SEC-P1-01 | No real Authenticator deployed | `NoopAuthenticator` is the default; rejects anonymous but cannot verify real identities | Deploy IdP + register real Authenticator before staging |
| SEC-P1-02 | No backup encryption configured | Drill in `BACKUP_RESTORE_RUNBOOK.md` could not test encryption | Provision KMS + age recipient key |
| SEC-P1-03 | No TLS at ingress | Uvicorn must not be directly exposed | Deploy reverse proxy with TLS |
| SEC-P1-04 | No egress allowlist | Default egress allows any destination | Configure egress firewall (allowlist model) |
| SEC-P1-05 | No CI secret scanning | `.github/workflows/clean-room.yml` does not include a secret-scanning step | Add `trufflehog` or `gitleaks` to CI (other engineer's area) |

### P2 — Important risks (before production)

| ID | Finding | Evidence | Mitigation |
|---|---|---|---|
| SEC-P2-01 | `context_ref` in approvals table may contain original tool arguments | `HITL_DURABLE_STORAGE_DESIGN.md` §line 56-59 | Treat DB as sensitive; restrict filesystem access; consider encrypting `context_ref` at application layer (separate design) |
| SEC-P2-02 | No automated retention/purge job | `HITL_PRODUCTION_OPERATIONS.md` §6 | Implement after product/legal sign-off (other engineer's area) |
| SEC-P2-03 | `Agent.run()` coroutine not resumable after crash | `HITL_DURABLE_STORAGE_DESIGN.md` §line 49-54 | Explicit `/resume` endpoint exists; document that operator must invoke manually |
| SEC-P2-04 | External side-effect atomicity gap | `HITL_DURABLE_STORAGE_DESIGN.md` §line 61-66 | Use provider idempotency keys; reconcile uncertain dispatches manually |

### P3 — Operational improvements

| ID | Finding | Mitigation |
|---|---|---|
| SEC-P3-01 | No `/api/v1/metrics` endpoint | Add Prometheus-format metrics endpoint (other engineer's area) |
| SEC-P3-02 | No `/api/v1/approvals/stats` endpoint | Add counts-by-status endpoint for monitoring (other engineer's area) |
| SEC-P3-03 | No health check for backup age | Alert if no verified backup within RPO (ops to configure) |
| SEC-P3-04 | No alerting on stale pending approvals | Alert if any approval is pending > 24h (ops to configure) |

---

## 7. Incident response for secret exposure

If a secret is suspected to be exposed:

1. **Revoke immediately** at the provider (OpenAI, Anthropic, etc.) or
   the IdP. Do NOT wait for confirmation.
2. **Rotate** the secret via the secret manager. Verify the new secret
   is in use and the old one is revoked.
3. **Investigate** the exposure vector: Git history, env files, logs,
   crash dumps, container images, backups.
4. **Audit** all approvals/decisions made between the exposure time and
   the revocation time. Flag any suspicious activity.
5. **Notify** the security team, product owner, and (if applicable)
   affected users per the incident response plan.
6. **Document** the incident: timeline, vector, scope, mitigation,
   lessons learned. Update this document if the vector was a gap in
   the runbook.

See `INCIDENT_RESPONSE.md` for the full incident runbook.

---

## 8. Separation of staging and production

| Dimension | Staging | Production | Verified? |
|---|---|---|---|
| Database | Separate file (e.g. `staging.db`) | Separate file (e.g. `prod.db`) | ☐ |
| Secrets | Separate keys (clearly labeled "staging") | Separate keys (clearly labeled "prod") | ☐ |
| Network | Separate subnet / VPC | Separate subnet / VPC | ☐ |
| Authenticator | Separate IdP client or namespace | Separate IdP client or namespace | ☐ |
| Provider keys | Staging keys (if any) — NEVER production keys | Production keys (restricted access) | ☐ |
| Backup target | Separate bucket/volume | Separate bucket/volume | ☐ |
| Log aggregation | Separate index/stream | Separate index/stream | ☐ |
| Operators | Staging operators (may include QA) | Production operators (restricted) | ☐ |

**Rule:** Staging and production MUST NOT share any of the above.
A staging compromise must NOT affect production data, secrets, or
operations.

---

## 9. What this review did NOT do

- **No penetration test** — only static review of code and config.
- **No network scan** — no staging host exists to scan.
- **No runtime secret scan** — no running deployment to inspect.
- **No CI secret-scanning configuration** — that's the other engineer's
  area (`.github/workflows/`).
- **No dependency CVE deep-dive** — Dependabot is enabled per commit
  `4c83885`; verifying Dependabot alerts is the other engineer's area.

This review is a STARTING POINT. A real staging deployment requires
the infrastructure to be provisioned and the controls above to be
verified against the running environment.
