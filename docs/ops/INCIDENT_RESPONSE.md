# Incident Response Runbook

**Status:** procedure documented. No real incidents have been responded
to using this runbook (no staging/production environment exists).

**Scope:** operational incidents affecting ALBRA SEED — process crashes,
DB corruption, credential exposure, external side-effect ambiguity,
backup failures.

**NOT in scope:** application code fixes (owned by the other engineer),
CI failures (owned by the other engineer).

---

## 1. Severity classification

| Severity | Definition | Response time | Escalation |
|---|---|---|---|
| **SEV-1** | Production data loss, credential compromise, or security breach | Immediate (15 min) | Page on-call + security lead + product owner |
| **SEV-2** | Staging unavailable, DB corruption, backup failure | 1 hour | Page on-call |
| **SEV-3** | Degraded service (slow, errors), stale pending approvals | 4 hours | Notify on-call (business hours) |
| **SEV-4** | Minor issue, non-blocking | Next business day | Ticket only |

---

## 2. Incident scenarios

### 2.1 Process crash / worker death

**Symptoms:** API returns 502/503; logs show process exit; systemd
restarts the service.

**Detection:** systemd `Restart=on-failure` auto-restarts; alert if
restart count > 3 in 5 minutes.

**Response:**

1. **Acknowledge** the alert. Open an incident record.
2. **Preserve evidence:** `journalctl -u albra-api --since "1 hour ago"`,
   core dumps (if any), DB state snapshot.
3. **Check DB state:** are there dispatches in `claimed` or `uncertain`?
   ```bash
   sqlite3 /var/lib/albra/approvals.db \
     "SELECT call_id, state, updated_at FROM dispatch_ledger WHERE state IN ('claimed','uncertain')"
   ```
4. **Convert `claimed` → `uncertain`** using the store's recovery method
   (`recover_interrupted_dispatches()`). DO NOT reset to `ready`.
5. **Reconcile each `uncertain` dispatch** per §3 below.
6. **Restart the service:** `sudo systemctl restart albra-api`.
7. **Verify health:** `GET /api/v1/health` returns `healthy`.
8. **Post-mortem:** root-cause the crash. If a code bug, file an issue
   for the other engineer. If an infra issue, address with infra team.

**Critical limit:** restarting the service does NOT resume a dead
`Agent.run()` coroutine. Only the explicit `/api/v1/approvals/{id}/resume`
endpoint can dispatch an approved call after a crash. The operator
must invoke it manually for each approved-but-not-dispatched call.

### 2.2 Service restart (planned)

**Symptoms:** planned restart for config change, code update, or maintenance.

**Response:**

1. **Notify** users/dependents of the planned restart window.
2. **Drain pending work:** wait for in-flight requests to complete
   (or timeout). Do NOT start new requests.
3. **Stop the service:** `sudo systemctl stop albra-api`.
4. **Verify no `claimed` dispatches remain:**
   ```bash
   sqlite3 /var/lib/albra/approvals.db \
     "SELECT COUNT(*) FROM dispatch_ledger WHERE state = 'claimed'"
   ```
   If any `claimed` exist, they were interrupted — convert to `uncertain`
   and reconcile per §3.
5. **Apply the change** (config, code, etc.).
6. **Start the service:** `sudo systemctl start albra-api`.
7. **Verify health.**
8. **Reconcile** any `uncertain` dispatches that resulted from the restart.

### 2.3 Database unavailable

**Symptoms:** API returns 500 on approval endpoints; logs show
`sqlite3.OperationalError: database is locked` or `database disk image
is malformed`.

**Response:**

1. **Acknowledge.** Open an incident.
2. **Check disk space:** `df -h /var/lib/albra`. If full, clear space
   (old logs, old backups) — but NEVER delete the live DB.
3. **Check DB integrity:**
   ```bash
   sqlite3 /var/lib/albra/approvals.db "PRAGMA integrity_check;"
   ```
4. **If `integrity_check` ≠ `ok`:** the DB is corrupt. Follow §2.4
   (DB corruption).
5. **If `database is locked`:** a writer is stuck. Check for zombie
   processes:
   ```bash
   ps aux | grep -E 'albra|uvicorn|sqlite'
   fuser /var/lib/albra/approvals.db
   ```
   Kill zombie writers ONLY after confirming they are not making progress.
6. **If disk is full:** alert; do NOT let the DB grow. Consider
   retention/purge (per `BACKUP_RESTORE_RUNBOOK.md` §5) — but only
   after legal/product sign-off.
7. **Restore from backup** if the DB is unrecoverable (per
   `BACKUP_RESTORE_RUNBOOK.md` §4).

### 2.4 Database corruption / schema incompatibility

**Symptoms:** `integrity_check` fails; `PRAGMA user_version` is unknown;
startup fails with schema error.

**Response:**

1. **STOP all workers.** Do not attempt to write to a corrupt DB.
2. **Preserve the corrupt DB** for investigation:
   ```bash
   cp -p /var/lib/albra/approvals.db /var/lib/albra/approvals.db.corrupt-$(date +%s)
   ```
3. **Restore from the most recent verified backup** per
   `BACKUP_RESTORE_RUNBOOK.md` §4.
4. **If schema version is unknown** (code is newer than DB): upgrade
   the code, do NOT downgrade the DB. If code is older than DB: upgrade
   the code to match, or roll back the DB to a version the code
   supports.
5. **Reconcile** all `uncertain` dispatches after restore.
6. **Post-mortem:** root-cause the corruption. Common causes:
   - Disk full during write.
   - Filesystem corruption.
   - Concurrent writers on a network FS (unsupported).
   - Process killed mid-write (use `Connection.backup()` for backups).

### 2.5 External operation with unknown result

**Symptoms:** a dispatch is in `uncertain` state — the worker may have
invoked the external service but died before recording the outcome.

**Detection:** alert on any dispatch entering `uncertain` state.

**Response:**

1. **Freeze retries** for that call ID. Do NOT allow automatic retry.
2. **Preserve evidence:** approval record, arg digest, timestamps,
   dispatch state, provider correlation ID.
3. **Query the provider's authoritative logs/events** using the
   provider idempotency key or correlation ID. Do NOT infer absence
   from a timeout or missing local response.
4. **Classify:**
   - **Confirmed applied:** record provider transaction/event ID; mark
     reconciled. Do NOT dispatch again.
   - **Confirmed not applied:** an authorized operator may retry AFTER
     revalidating the same approved call, deadline, policy, firewall,
     and provider idempotency semantics. Do NOT reset the existing
     consumed claim.
   - **Cannot determine:** leave `uncertain`, escalate, request
     business-owner decision. No automatic retry.
5. **If retry is approved:** create a NEW, explicitly linked
   operation/approval; preserve old call ID + evidence. Reuse original
   provider idempotency key ONLY when the provider explicitly guarantees
   the desired semantics.
6. **Record** decision, operator, evidence links, provider references,
   timestamps in a tamper-evident audit system. Never log secrets or
   full sensitive arguments.

### 2.6 Restoration of an old backup

**Symptoms:** a restore from an old backup is needed (data corruption,
accidental deletion, regulatory requirement).

**Response:**

1. **Open an incident/change record.** Identify the restore point and
   authorized operator.
2. **Preserve the current DB** for investigation.
3. **Stop all workers** per `BACKUP_RESTORE_RUNBOOK.md` §4.
4. **Fence the old instance** so it cannot resume work.
5. **Retrieve + decrypt** the backup per `BACKUP_RESTORE_RUNBOOK.md` §4.
6. **Verify integrity + schema version** before placing.
7. **Restore to a NEW path** (never overwrite the live path).
8. **Identify dispatches in `claimed`/`uncertain`.** Convert
   `claimed` → `uncertain`. Reconcile each per §2.5.
9. **Run read-only smoke tests.**
10. **Re-enable ONE canary worker.** Observe errors + dispatch states.
11. **Restore full traffic** only after sign-off.
12. **Record** restore duration, recovered point, missing records,
    uncertain effects, operator, approver, timestamp. Do NOT claim
    zero data loss unless verified.

### 2.7 Loss of provider connectivity

**Symptoms:** provider calls return network errors or timeouts; logs
show `httpx.ConnectError` or `httpx.TimeoutException`.

**Response:**

1. **Check egress firewall:** is the provider endpoint in the allowlist?
2. **Check DNS:** `dig api.openai.com` (or the relevant provider).
3. **Check provider status page:** is there a provider-side outage?
4. **If provider is down:** do NOT retry automatically. Queue
   approvals as `pending` until connectivity is restored.
5. **If egress is blocked:** work with infra to restore allowlist.
6. **If DNS is broken:** work with infra to fix DNS.
7. **Verify** connectivity is restored before resuming normal operation.

### 2.8 Credential revocation

**Symptoms:** a credential is suspected compromised (or rotated per
policy).

**Response:**

1. **Revoke immediately** at the provider / IdP. Do NOT wait for
   confirmation.
2. **Rotate** via the secret manager. Verify the new credential is in
   use.
3. **Restart** the service to pick up the new credential (or use
   hot-reload if supported).
4. **Audit** approvals/decisions made between the exposure time and
   the revocation time. Flag any suspicious activity.
5. **Notify** security team, product owner, affected users per the
   incident plan.
6. **Document** the incident per §6.

### 2.9 Recovery after a security incident

**Symptoms:** confirmed breach (unauthorized access, data exfiltration,
malware).

**Response:**

1. **Activate the incident response team.** Follow the org's IR plan.
2. **Isolate** the affected host(s). Revoke all credentials. Block
   egress.
3. **Preserve evidence:** disk images, logs, DB snapshots. Do NOT
   delete anything — it may be needed for forensics.
4. **Assess scope:** which approvals, decisions, tool arguments were
   accessed? Which provider keys were used?
5. **Rotate ALL credentials** that the compromised host had access to.
6. **Restore** from a known-clean backup (per `BACKUP_RESTORE_RUNBOOK.md`).
7. **Patch** the vulnerability that allowed the breach.
8. **Notify** affected users, regulators (if required by law), and
   partners per the IR plan.
9. **Post-mortem:** document timeline, vector, scope, mitigation,
   lessons learned. Update this runbook if the vector was a gap.

---

## 3. Reconciliation procedure for uncertain dispatches

(See also `BACKUP_RESTORE_RUNBOOK.md` §6 — duplicated here for
incident responders who may not have the other doc open.)

For each `uncertain` dispatch:

1. **Freeze retries** for that call ID.
2. **Preserve evidence:** approval record, arg digest, timestamps,
   dispatch state, provider correlation ID.
3. **Query the provider** (per §2.5 step 3).
4. **Classify** (per §2.5 step 4).
5. **If retry approved:** create a NEW linked operation; do NOT reset
   the existing consumed claim.
6. **Record** decision, operator, evidence, provider references,
   timestamps in the audit system.

---

## 4. Escalation matrix

| Severity | On-call | Security lead | Product owner | Infra | Other engineer |
|---|---|---|---|---|---|
| SEV-1 | ✅ Page | ✅ Page | ✅ Notify | ✅ Page | ✅ Notify (if code bug) |
| SEV-2 | ✅ Page | ☐ Notify | ☐ Notify | ✅ Notify | ☐ Notify (if code bug) |
| SEV-3 | ✅ Notify (business hrs) | — | — | ☐ Notify (if infra) | — |
| SEV-4 | ☐ Ticket | — | — | — | — |

---

## 5. Evidence preservation

During an incident, preserve:

- `journalctl -u albra-api --since "<incident start>" > /tmp/incident-api.log`
- `cp -p /var/lib/albra/approvals.db /tmp/incident-approvals-$(date +%s).db`
- `cp -p /var/log/albra/agent.log /tmp/incident-agent.log`
- `cp -p /var/log/albra/audit.jsonl /tmp/incident-audit.jsonl`
- `ps aux > /tmp/incident-ps.txt`
- `ss -tlnp > /tmp/incident-ports.txt`
- `dmesg > /tmp/incident-dmesg.txt` (if relevant)

Store evidence in a private, access-controlled location. Do NOT upload
to public storage. Do NOT include secrets in evidence (redact if
necessary).

---

## 6. Incident record template

```
Incident ID: INC-YYYYMMDD-NN
Severity: SEV-1 / SEV-2 / SEV-3 / SEV-4
Start time (UTC):
Detected by:
Acknowledged by:
Resolved by:

Summary:
  <1-2 sentence description>

Timeline:
  - <timestamp>: <event>
  - <timestamp>: <event>
  ...

Root cause:
  <detailed analysis>

Scope:
  - Affected approvals: <count or "none">
  - Affected dispatches: <count or "none">
  - Affected users: <count or "none">
  - Data loss: <yes/no + details>

Mitigation:
  - <action taken>

Reconciliation:
  - Uncertain dispatches: <count>
  - Reconciled: <count + evidence links>
  - Pending reconciliation: <count>

Evidence:
  - Logs: <path>
  - DB snapshot: <path>
  - Provider evidence: <links>

Lessons learned:
  - <what to improve>

Action items:
  - [ ] <action> — owner — due date
  - [ ] <action> — owner — due date

Sign-off:
  - On-call: <name> — <date>
  - Security: <name> — <date>
  - Product: <name> — <date>
```

---

## 7. Observability signals to monitor

### 7.1 Available signals (from the API)

| Signal | Endpoint | Alert condition |
|---|---|---|
| API health | `GET /api/v1/health` | `status != "healthy"` or endpoint unreachable |
| API readiness | `GET /api/v1/readiness` | `ready != true` |
| Component status | `GET /api/v1/health` → `components` | any component `!= "healthy"` |

### 7.2 Signals requiring DB access (operator-side)

| Signal | Query | Alert condition |
|---|---|---|
| Pending approvals count | `SELECT COUNT(*) FROM approvals WHERE status='pending'` | > 0 for > 24h (stale) |
| Uncertain dispatches | `SELECT COUNT(*) FROM dispatch_ledger WHERE state='uncertain'` | > 0 (requires reconciliation) |
| Claimed dispatches (stale) | `SELECT COUNT(*) FROM dispatch_ledger WHERE state='claimed' AND updated_at < datetime('now', '-1 hour')` | > 0 (worker may be stuck) |
| DB size | `stat -c%s /var/lib/albra/approvals.db` | growing unexpectedly |
| DB integrity | `PRAGMA integrity_check` | ≠ `ok` |
| Schema version | `PRAGMA user_version` | unknown / unexpected |

### 7.3 Signals requiring infra access

| Signal | Source | Alert condition |
|---|---|---|
| Disk space | `df -h /var/lib/albra` | > 80% full |
| CPU / memory | systemd / node exporter | sustained high usage |
| Log errors | log aggregator (Loki/CloudWatch) | error rate spike |
| Backup age | backup target | no verified backup within RPO (24h) |
| Backup failure | backup job log | any failure |
| TLS cert expiry | reverse proxy | < 30 days to expiry |
| Egress violations | egress firewall logs | any denied connection to non-allowlisted dest |

### 7.4 Signals requiring implementation (other engineer's area)

| Signal | Why | Owner |
|---|---|---|
| `/api/v1/metrics` (Prometheus) | Native metrics without OTel collector | other engineer |
| `/api/v1/approvals/stats` | Counts by status without DB access | other engineer |
| OTel traces | Distributed tracing | other engineer |

Do NOT alert on signals that are not yet implemented. Distinguish
"available today" from "requires implementation."

---

## 8. Alert routing

| Alert | Route | Severity |
|---|---|---|
| API health ≠ healthy | Page on-call | SEV-2 |
| API readiness ≠ ready | Page on-call | SEV-2 |
| DB integrity ≠ ok | Page on-call + security | SEV-1 |
| Uncertain dispatch > 0 | Page on-call | SEV-2 |
| Stale pending approval > 24h | Notify on-call (business hrs) | SEV-3 |
| Disk > 80% full | Notify on-call | SEV-3 |
| Backup age > RPO | Notify on-call | SEV-3 |
| Backup failure | Notify on-call | SEV-2 |
| TLS cert < 30 days | Notify on-call | SEV-3 |
| Egress denied to non-allowlisted | Page on-call + security | SEV-2 |
| Auth failure spike | Page on-call + security | SEV-2 |

---

## 9. What this runbook does NOT cover

- **Application code fixes** — if an incident root-cause is a code bug,
  file an issue for the other engineer. Do NOT fix it on this branch.
- **CI failures** — owned by the other engineer (`.github/workflows/`).
- **Dependency vulnerabilities** — Dependabot is enabled; verifying
  alerts is the other engineer's area.
- **Real-time provider reconciliation** — requires provider-specific
  tooling not available in this repo. Documented procedure only.
- **Legal/regulatory notification** — follow the org's IR plan; this
  runbook does not replace legal counsel.

---

## 10. Limits of the system (must respect)

Per `HITL_DURABLE_STORAGE_DESIGN.md`:

- **Restarting the service is NOT recovery of a dead `Agent.run()` coroutine.**
  Only the explicit `/resume` endpoint can dispatch an approved call.
- **A durable approval row does NOT serialize the suspended coroutine.**
  After a process restart, approval status can be recovered, but the
  original run is NOT automatically resumed.
- **External side effects are NOT atomic with DB commits.** A crash
  after an external call may leave the dispatch in `uncertain` state.
- **SQLite is single-writer.** All workers must share the same DB on
  the same host with reliable locking.
- **Per-worker DBs are NOT cross-worker safe.** Workers must point at
  the same DB file.
- **Claims are NEVER automatically reclaimed.** An operator must
  explicitly convert `claimed` → `uncertain` and reconcile.
- **`uncertain` dispatches are NEVER automatically retried.** Manual
  reconciliation per §3 is required.

Do NOT design procedures that violate these limits. Do NOT claim
"automatic recovery" or "zero data loss" without verified evidence.
