# Backup and Restore Runbook

**Status:** procedure documented; real-DB drill NOT YET executed against
a production or staging database. A test-DB drill was executed and
passed (see §11).

**Scope:** SQLite approval database (`ALBRA_APPROVAL_DB_PATH`), SQLite
dispatch ledger, audit logs, and configuration files.

**NOT in scope:** application code, tests, CI workflows, the HITL engine
itself (owned by the other engineer).

---

## 1. What must be backed up

| Data | Location | Sensitive? | Why backed up |
|---|---|---|---|
| **SQLite approval DB** | `$ALBRA_APPROVAL_DB_PATH` (e.g. `/var/lib/albra/approvals.db`) | YES — contains tool arguments, RunContext snapshot, decision actor identities | Required for HITL recovery after crash/restart |
| **SQLite WAL/SHM sidecars** | Same dir as DB (`approvals.db-wal`, `approvals.db-shm`) | YES | Required for crash-consistent restore; safe to omit if backup uses `Connection.backup()` |
| **SQLite dispatch ledger** | Same DB or separate path (per `SQLiteSuspendedExecutionCoordinator`) | YES — contains arg digests, call IDs, state | Required for one-shot claim guarantee |
| **Audit log** | `$ALBRA_AUDIT_LOG_PATH` (default `./logs/audit.jsonl`) | YES — may contain redacted but sensitive events | Required for incident investigation |
| **Agent log** | `$ALBRA_LOG_PATH` (default `./logs/agent.log`) | YES — may contain redacted but sensitive content | Required for debugging |
| **Config file** (`.env` or env-injected) | Deployment secret manager | YES — contains API keys | Required to reproduce deployment; NEVER store in Git |
| **Schema version** | `PRAGMA user_version` in the SQLite DB | NO | Required to detect schema incompatibility on restore |

**Memory store** (`ALBRA_MEMORY_PATH`): if the deployment uses
`memory_backend=sqlite`, the memory DB should also be backed up. If
`memory_backend=memory` (in-memory), data is NOT persisted and does not
need backup.

---

## 2. What must NOT be backed up (or must be redacted)

- **Raw API keys** (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) — never
  in backups; inject via secret manager at restore time.
- **Full sensitive tool arguments** — the `context_ref` column in the
  `approvals` table may contain original tool arguments. Treat the
  backup as sensitive and restrict access.
- **Bearer tokens / session cookies** — never in DB or logs; if found,
  treat as a security incident.
- **Crash dumps / core files** — may contain in-memory secrets; exclude
  from backups.

---

## 3. Backup procedure (safe — uses SQLite online backup API)

**Why `Connection.backup()` and not `cp`:** SQLite in WAL mode writes
to `-wal` and `-shm` sidecars. A raw `cp` of the `.db` file while WAL
is active produces an inconsistent snapshot. `sqlite3.Connection.backup()`
uses SQLite's online backup API, which is crash-consistent.

### Prerequisites

- The backup operator has read access to `$ALBRA_APPROVAL_DB_PATH`.
- A private temporary directory (mode `0700`) exists for staging.
- The backup target (object storage or independent volume) is reachable.
- The encryption key is available in the secret manager (NOT in env
  files, NOT in command-line arguments, NOT in the backup manifest).

### Steps

```bash
# 1. Acquire the backup-job lock (prevent concurrent backups).
#    On a single-host deployment, use flock:
LOCK=/var/lock/albra-backup.lock
exec 9>"$LOCK" || exit 1
flock -n 9 || { echo "backup already running"; exit 1; }

# 2. Create a private staging directory.
STAGE=$(mktemp -d -m 0700 /tmp/albra-backup.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT

# 3. Run the Python backup (uses sqlite3.Connection.backup()).
python3 - <<'PY'
import os, sqlite3, hashlib, time
src = os.environ["ALBRA_APPROVAL_DB_PATH"]
dst = "/tmp/albra-backup.XXXXXX/snapshot.db"  # replace with $STAGE
src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
dst_conn = sqlite3.connect(dst)
src_conn.backup(dst_conn)
dst_conn.close()
src_conn.close()

# Verify integrity.
v = sqlite3.connect(dst)
assert v.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
schema_version = v.execute("PRAGMA user_version").fetchone()[0]
counts = {t: v.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("approvals", "dispatch_ledger")}
v.close()

# Compute checksum of the plaintext snapshot (for manifest; NOT uploaded in plaintext).
with open(dst, "rb") as f:
    sha256 = hashlib.sha256(f.read()).hexdigest()

print(f"schema_version={schema_version}")
print(f"counts={counts}")
print(f"sha256={sha256}")
print(f"size={os.path.getsize(dst)}")
print(f"timestamp={time.time()}")
PY

# 4. Encrypt the snapshot before upload.
#    Use age (recommended), gpg, or cloud KMS envelope encryption.
#    NEVER upload the plaintext snapshot.
age -r "$BACKUP_RECIPIENT_KEY" "$STAGE/snapshot.db" > "$STAGE/snapshot.db.age"

# 5. Compute the checksum of the ENCRYPTED artifact (for manifest).
ENC_SHA=$(sha256sum "$STAGE/snapshot.db.age" | awk '{print $1}')
ENC_SIZE=$(stat -c%s "$STAGE/snapshot.db.age")

# 6. Upload the encrypted artifact + a manifest (NO secrets, NO payloads).
#    The manifest contains: timestamp, schema version, encrypted size,
#    encrypted sha256, plaintext sha256 (optional, for tamper detection).
cat > "$STAGE/manifest.json" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "schema_version": $SCHEMA_VERSION,
  "counts": $COUNTS,
  "encrypted_size": $ENC_SIZE,
  "encrypted_sha256": "$ENC_SHA"
}
EOF

# 7. Upload to the backup target (object storage, independent volume).
#    Verify the uploaded checksum matches the manifest.
aws s3 cp "$STAGE/snapshot.db.age" "s3://$BACKUP_BUCKET/$(date +%Y/%m/%d)/snapshot.db.age"
aws s3 cp "$STAGE/manifest.json"  "s3://$BACKUP_BUCKET/$(date +%Y/%m/%d)/manifest.json"

# 8. Verify immutability / retention settings on the uploaded objects.
# 9. Remove plaintext staging files (trap already does this on exit).
```

### Expected results

- `integrity_check=ok` — backup is crash-consistent.
- `schema_version` matches the current code's expected version.
- `counts` match the live DB (no rows lost).
- `encrypted_sha256` matches the uploaded object's checksum.
- Backup duration < 10 seconds for typical DB sizes (< 100 MB).

### Failure modes

| Symptom | Cause | Action |
|---|---|---|
| `DatabaseError: database is locked` | A long-running writer holds the lock | Retry with backoff; alert if persistent |
| `integrity_check` returns anything other than `ok` | DB corruption | Do NOT upload; escalate per `INCIDENT_RESPONSE.md` |
| `schema_version` is unknown | Code and DB are out of sync | Do NOT restore; upgrade code first or rollback DB |
| `encrypted_sha256` mismatch after upload | Corrupt upload | Re-upload; alert if persistent |

---

## 4. Restore procedure (to an isolated path — never overwrite live)

### Prerequisites

- An incident/change record is open.
- The authorized restore operator is identified.
- All workers are STOPPED (no writer can interfere).
- The old instance is FENCED (cannot resume work after restore).
- The decryption key is available in the secret manager.

### Steps

```bash
# 1. Open an incident/change record.
#    Record: restore point, authorized operator, reason, timestamp.

# 2. Stop ALL workers, API instances, schedulers, and dispatch consumers.
sudo systemctl stop albra-api albra-worker albra-scheduler

# 3. Fence the old instance (prevent it from resuming after restore).
#    e.g. revoke its credentials, block its egress, or power it off.

# 4. Preserve the current DB for investigation (do NOT delete).
cp -p "$ALBRA_APPROVAL_DB_PATH" "$ALBRA_APPROVAL_DB_PATH.pre-restore-$(date +%s)"

# 5. Retrieve + decrypt the backup into a PRIVATE temp dir (mode 0700).
RESTORE_STAGE=$(mktemp -d -m 0700 /tmp/albra-restore.XXXXXX)
trap 'rm -rf "$RESTORE_STAGE"' EXIT

aws s3 cp "s3://$BACKUP_BUCKET/$RESTORE_DATE/snapshot.db.age" "$RESTORE_STAGE/"
age --decrypt -i "$DECRYPTION_KEY" "$RESTORE_STAGE/snapshot.db.age" > "$RESTORE_STAGE/snapshot.db"

# 6. Verify the restored DB BEFORE placing it.
python3 - <<PY
import sqlite3, json
c = sqlite3.connect("$RESTORE_STAGE/snapshot.db")
assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
v = c.execute("PRAGMA user_version").fetchone()[0]
counts = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("approvals", "dispatch_ledger")}
print(f"schema_version={v}")
print(f"counts={counts}")
# Verify no rows were lost (compare to manifest).
manifest = json.load(open("$RESTORE_STAGE/manifest.json"))
assert counts == manifest["counts"], f"COUNT MISMATCH: {counts} vs {manifest['counts']}"
c.close()
PY

# 7. Restore to a NEW path first (never overwrite the live path).
NEW_DB="$ALBRA_APPROVAL_DB_PATH.restored-$(date +%s)"
cp -p "$RESTORE_STAGE/snapshot.db" "$NEW_DB"
chmod 0600 "$NEW_DB"
chown albra:albra "$NEW_DB"

# 8. Identify dispatches in claimed/uncertain state.
#    Convert interrupted claims to uncertain using the store's recovery method.
#    NEVER reset them to ready or delete ledger rows.
python3 - <<PY
import sqlite3
c = sqlite3.connect("$NEW_DB")
rows = c.execute("SELECT call_id, state FROM dispatch_ledger WHERE state IN ('claimed','uncertain')").fetchall()
for call_id, state in rows:
    print(f"  {call_id}: {state}")
# Reconcile each uncertain dispatch per §6 before allowing execution.
c.close()
PY

# 9. Point ALBRA_APPROVAL_DB_PATH at the new DB (via env or config).
#    Restart ONE canary worker. Observe errors and dispatch states.

# 10. Run read-only smoke tests (per ACCEPTANCE_EVIDENCE.md).

# 11. Re-enable full traffic only after sign-off.

# 12. Record: restore duration, recovered point, missing records,
#     uncertain effects, operator, approver, timestamp.
```

### Critical rules

- **NEVER** overwrite the live DB path. Always restore to a new path.
- **NEVER** reset `claimed` or `uncertain` dispatches to `ready`. Convert `claimed` → `uncertain` and reconcile manually.
- **NEVER** delete ledger rows to "make the queue run".
- **NEVER** decrypt into a shared directory.
- **NEVER** restore without stopping all workers first.
- **NEVER** claim zero data loss unless verified against provider evidence.

---

## 5. Retention and deletion (PROPOSED — subject to legal/product sign-off)

| Data type | Live retention | Archive | Total | Notes |
|---|---|---|---|---|
| Pending approvals | Until decision or expiry | — | — | Alert on stale pending (>24h) |
| Approved-but-not-dispatched | No auto-deletion | — | — | Requires explicit resume or expiry |
| Ready/claimed/uncertain dispatches | No auto-deletion | — | — | Uncertain requires reconciliation |
| Completed/rejected/expired/failed | 90 days in live DB | 275 days encrypted archive | 365 days | Subject to legal hold |
| Audit log | 90 days in live | 275 days encrypted | 365 days | May be longer per regulation |
| Backups | — | 35 days rolling + 12 monthly + quarterly drill | — | Quarterly restore drill mandatory |

**RPO proposal:** 24 hours (daily encrypted backups).
**RTO proposal:** 4 hours (restore + reconcile + canary).

These are PROPOSALS — they must be approved by product, legal, and
security owners before adoption. Do NOT present them as agreed targets.

### Purge procedure (NOT automated — manual after sign-off)

1. Verify the encrypted archive exists and a restore sample succeeds.
2. Produce counts by status/age (do NOT print payloads).
3. Confirm `uncertain`/`pending` records are excluded from the purge set.
4. Execute a transactional `DELETE` with foreign-key checks + a manifest
   of IDs/counts (NOT payloads).
5. Log the purge: operator, timestamp, IDs purged, manifest hash.
6. Never manually edit production SQLite tables or run ad-hoc `DELETE`.

---

## 6. Reconciliation of uncertain dispatches

After restore (or after a crash), some dispatches may be in `uncertain`
state — the worker may have invoked an external service but died before
recording the outcome. These MUST be reconciled before any retry.

### Steps

1. **Freeze retries** for the call ID. Preserve approval evidence, arg
   digest, timestamps, dispatch state.
2. **Query the provider's authoritative logs/events** using the provider
   idempotency key or correlation ID. Do NOT infer absence from a timeout
   or missing local response.
3. **Classify the result:**
   - **Confirmed applied:** record provider transaction/event ID; mark
     reconciled. Do NOT dispatch again.
   - **Confirmed not applied:** an authorized operator may retry AFTER
     revalidating the same approved call, deadline, policy, firewall,
     and provider idempotency semantics. Do NOT reset the existing
     consumed claim.
   - **Cannot determine:** leave `uncertain`, escalate, request
     business-owner decision. No automatic retry.
4. **If retry is approved:** create a NEW, explicitly linked
   operation/approval; preserve old call ID + evidence. Reuse original
   provider idempotency key ONLY when the provider explicitly guarantees
   the desired semantics.
5. **Record** decision, operator identity, evidence links, provider
   references, timestamps in a tamper-evident audit/incident system.
   Never log secrets or full sensitive arguments.

---

## 7. Encryption and key management

### Required baseline

1. **Database at rest:** encrypted persistent volume (host full-disk
   encryption or cloud block-storage encryption backed by managed KMS).
2. **Backups:** encrypted independently BEFORE they leave the trusted
   host/account. Use `age` with an offline recipient key, or
   object-storage/KMS encryption with a separately controlled key.
3. **Key material:** NEVER in Git, env files, container images,
   command-line arguments, application logs, or SQLite.
4. **Key separation:** runtime can read/write the live DB; backup job
   can create + upload encrypted snapshots; restore operator can
   retrieve/decrypt; KMS admins are distinct from application operators.
5. **Rotation:** use the KMS/encryption system's supported re-encryption
   procedure. Prove old backups remain restorable before retiring an
   old key. Key loss = backup loss.
6. **Disk encryption ≠ host security:** disk encryption protects lost
   media, not a compromised running host. For higher-risk deployments,
   consider SQLCipher only after a separate design + migration review.

### Go/no-go

If sensitive approval arguments are present AND neither encrypted
storage NOR independently encrypted backups are configured and verified,
**do NOT deploy production workloads.**

---

## 8. Consistency considerations

| Concern | Mitigation |
|---|---|
| SQLite WAL mode + concurrent writers | One DB shared by all workers on the SAME host with reliable SQLite locking. Do NOT use network FS. |
| Backup during active write | `Connection.backup()` is crash-consistent; safe to run while writers are active. |
| Restore while writers active | STOP ALL WRITERS before restore. Fence the old instance. |
| Schema version mismatch | `PRAGMA user_version` checked on backup AND restore. Unknown versions fail closed. |
| WAL/SHM sidecars on restore | Restore only the `.db` file; SQLite recreates `-wal`/`-shm` on first open. |
| Cross-worker approval decisions | All workers must point at the SAME SQLite DB file. Per-worker DBs are NOT cross-worker safe. |

---

## 9. RPO and RTO proposals (NOT agreed — subject to approval)

| Metric | Proposal | Justification | Approval required from |
|---|---|---|---|
| **RPO** (recovery point objective) | 24 hours | Daily encrypted backups; alert if no verified backup exists within 24h | product + security |
| **RTO** (recovery time objective) | 4 hours | Stop workers + restore + reconcile uncertain + canary | ops + product |

These are STARTING POINTS. The actual RPO/RTO must be set by the
product/security owners based on business impact, regulatory
requirements, and contractual SLAs. Do NOT present these as agreed
targets until signed off.

---

## 10. Independent storage

- **Backups MUST be stored independently** from the production system.
  If the production host is lost, backups must survive.
- Use object storage (S3, GCS, Azure Blob) in a DIFFERENT account/region
  when possible, OR an independent volume on a different host.
- The backup target must NOT be able to decrypt backups (envelope
  encryption with a key the target cannot access).
- Test: simulate losing the production host; verify backups are
  retrievable + restorable from the independent target.

---

## 11. Drill evidence (executed 2026-10-14)

A safe, self-contained backup/restore drill was executed using a TEST
SQLite database (NOT a production database).

**Script:** `scripts/ops_backup_drill.py`
**Full output:** `download/ops_backup_drill_output.txt`

### Summary

| Step | Result |
|---|---|
| Create test DB (1 approval, 1 dispatch, schema v1) | ✅ PASS |
| Online backup via `sqlite3.Connection.backup()` | ✅ PASS (0.3 ms, 20480 bytes) |
| Backup integrity check | ✅ `integrity_check=ok`, schema v1, row counts match |
| Restore to isolated path (mode 0600) | ✅ PASS |
| Restored DB integrity + row counts + sample row | ✅ PASS |
| Negative test: corrupt DB header → `DatabaseError` | ✅ PASS (fail-closed) |
| Negative test: random bytes (wrong key) → `DatabaseError` | ✅ PASS (fail-closed) |

### Drill limitations

- **Test DB, not production DB** — no real approval payloads, no real
  tool arguments, no real provider calls.
- **Encryption NOT tested** — requires KMS / envelope-encryption setup
  not available in this environment.
- **Concurrent-write scenario NOT tested** — requires a running worker
  writing to the DB during backup.
- **Cross-worker scenario NOT tested** — requires multiple worker
  processes pointing at the same DB.
- **Real provider reconciliation NOT tested** — no real provider was
  contacted.

### What remains PENDING

- [ ] Real-DB backup/restore drill (requires staging host).
- [ ] Encryption drill (requires KMS).
- [ ] Concurrent-write drill (requires running worker).
- [ ] Cross-worker decision observation (requires multi-worker setup).
- [ ] Provider reconciliation drill (requires staging provider).

These are marked `PENDIENTE DE EJECUCIÓN` — they require infrastructure
that is not available in this environment.
