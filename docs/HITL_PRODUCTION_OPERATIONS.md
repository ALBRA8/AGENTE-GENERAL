# HITL Production Operations Runbook

Status: deployment policy and acceptance procedure. This document is not evidence that a particular production environment has been inspected or tested.

## 1. Scope and limitations

The SQLite approval database stores serialized ApprovalRequest records. The current request snapshot can contain original tool arguments and RunContext data. POSIX file mode 0600 and parent directory mode 0700 are useful local controls, not encryption. A host administrator, stolen disk, copied backup, or process running as the service account can read plaintext data.

The dispatch ledger prevents automatic replay after an interrupted claim. It cannot atomically commit an arbitrary remote side effect and a local SQLite result. State uncertain means the provider may have accepted the effect; do not retry until reconciled. CI does not validate a real deployment, identity provider, reverse proxy, firewall, KMS, or external provider.

## 2. Encryption and key management

### Required production baseline

1. Place the database, SQLite WAL/SHM sidecars, temporary files, and operational logs on an encrypted persistent volume (host full-disk encryption or cloud block-storage encryption backed by a managed KMS). Never store the database in a source checkout, container image, public/shared volume, or unencrypted network share.
2. Encrypt backups independently before they leave the trusted host/account. Use the organization's approved envelope-encryption tool, such as age with an offline recipient key or object-storage/KMS encryption with a separately controlled key. The backup target must not be able to decrypt backups unless policy explicitly permits it.
3. Keep key material out of Git, committed environment files, container images, command-line arguments, application logs, and SQLite. Inject secrets using the deployment secret manager or workload identity. Never log tokens or key values.
4. Separate roles: runtime can read/write the live database; backup job can create and upload encrypted snapshots; restore operator can retrieve/decrypt backups; KMS/key administrators are distinct from application operators.
5. Record key owner, key ID/version, purpose, rotation interval, recovery custodian, and revocation process in the secret inventory. Rotate using the KMS/encryption system's supported re-encryption procedure. Prove old backups remain restorable before retiring an old key. Key loss means backup loss.
6. Disk encryption protects lost media and offline snapshots, not a compromised running host. For higher-risk deployments, consider a vetted application-layer encrypted database/SQLCipher only after a separate design and migration review. Do not invent custom cryptography or silently change SQLite storage format.

Go/no-go: if sensitive approval arguments are present and neither encrypted storage nor independently encrypted backups is configured and verified, do not deploy production workloads.

## 3. Database permissions and placement

- Set ALBRA_APPROVAL_DB_PATH to an absolute path on the approved persistent encrypted volume.
- Use one database shared by cooperating processes on the same host/filesystem with reliable SQLite locking. Do not give each worker a separate DB. Do not assume network filesystems support WAL/SQLite locking correctly.
- Confirm service UID/GID owns the directory and database; directory is private to the service account and database is mode 0600 on POSIX. Check WAL/SHM sidecars and backup staging directories too.
- Reject symlinked database paths; avoid writable parent directories for untrusted users.
- Run only one migration/restore operator at a time. Stop all workers before restore or destructive maintenance.
- Alert on database open errors, corrupt records, unsupported schema versions, disk exhaustion, backup age, failed backup verification, and uncertain dispatches.

## 4. Backup procedure

Use SQLite's online backup API, not a raw copy of the live database while WAL mode is active. Python's sqlite3 Connection.backup() creates a consistent snapshot.

1. Acquire the backup-job lock and create a private temporary directory (0700).
2. Open the live DB read-only where practical and use Connection.backup() into a temporary SQLite file.
3. Run PRAGMA integrity_check and verify PRAGMA user_version is a supported schema version.
4. Encrypt the snapshot before upload. Upload only the encrypted artifact plus a manifest containing timestamp, schema version, size, and checksum of the encrypted artifact (not approval payloads).
5. Verify uploaded checksum and retention/immutability settings. Remove plaintext staging files on success and failure. Restrict crash dumps and filesystem snapshots.
6. Alert if no verified backup exists within the defined RPO.

Initial policy recommendation, subject to contractual/legal requirements: encrypted daily backups, 35-day rolling retention, one encrypted monthly copy for 12 months, and a quarterly restore drill. Set RPO/RTO explicitly; these values are a starting point, not a guarantee. A backup retention job must never override a legal hold.

## 5. Restore and disaster recovery

1. Open an incident/change record; identify the restore point and authorized operator. Preserve current DB and encrypted backup metadata for investigation.
2. Stop all workers, API instances, schedulers, and dispatch consumers. Fence the old instance so it cannot resume work after restoration.
3. Retrieve and decrypt the backup using the approved key workflow into a private temporary directory. Never decrypt into a shared directory.
4. Run PRAGMA integrity_check, confirm PRAGMA user_version is supported, and inspect counts of approval and dispatch states. Integrity failures are a hard stop.
5. Restore to a new path first. Verify owner/mode, parent permissions, and storage encryption. Keep the original untouched until sign-off.
6. Before allowing execution, identify dispatches in claimed or uncertain. Convert interrupted claims to uncertain using the supported store recovery method. Never reset them to ready or delete ledger rows to make the queue run.
7. Reconcile uncertain effects with authoritative provider event/transaction history using stable call ID/idempotency key, timestamp, destination, and payload digest. Record operator, evidence, decision, provider reference, and time in the incident/audit system. If the provider cannot prove the effect did not occur, do not retry blindly. If provider idempotency exists, use the exact same key and arguments according to documented provider semantics.
8. Run read-only smoke tests, confirm API authentication/RBAC and firewall policy, then re-enable one canary worker. Observe errors and dispatch states before restoring full traffic.
9. Record restore duration, recovered point, missing records, uncertain effects, and sign-off. Do not claim zero data loss unless verified.

## 6. Retention and deletion policy

Proposed baseline, subject to privacy, contractual, and legal review:

- Pending approvals: retain until decision or expiry; alert on stale pending rows.
- Approved-but-not-dispatched and ready/claimed/uncertain dispatches: no automatic deletion. An uncertain dispatch requires explicit reconciliation and evidence.
- Completed, rejected, expired, or safely failed terminal approvals: retain 90 days in the live database, then archive into encrypted, access-controlled evidence storage for a further 275 days (365 days total), unless a legal hold or stricter policy applies.
- Audit/evidence records required by regulation or incident policy take precedence over these defaults.
- At end of retention, perform an approved, logged purge only after verifying the encrypted archive and restore sample. Delete corresponding terminal approval/dispatch records transactionally with foreign-key checks and a manifest of IDs/counts, not payloads. Never manually edit production SQLite tables or run ad-hoc DELETE statements.
- Backups containing deleted records expire under backup retention too; document the maximum period before data ages out of all backups.

Do not deploy an automated purge job until product/legal owners approve the retention period and an implementation with tests exists. This repository does not currently ship an automatic retention/purge engine.

## 7. Authentication, authorization, and firewall

### Identity/authentication

- Register a real, production-supported Authenticator before starting the runtime. The default NoopAuthenticator is mock/anonymous and is not acceptable for approval APIs.
- Terminate TLS at an approved ingress/reverse proxy; require TLS between proxy and app where traffic crosses a trust boundary. Do not expose Uvicorn directly to the public internet.
- Use short-lived tokens or workload identity, validate issuer/audience/expiry/signature in the real authenticator, rotate credentials, and avoid bearer tokens in URLs or logs.
- Verify unauthenticated and invalid-token requests return 401. Verify valid identities are stable and decision actor identity comes from the verified principal, not request JSON.

### Authorization

- Create separate, narrowly scoped policy grants for approval:list, approval:decide, and approval:resume. Deny by default. Do not grant all three to every service account.
- Test that a valid identity without each permission gets 403; a properly scoped approver gets only the allowed operation; a non-approver cannot decide or resume; expired/revoked credentials fail.
- Use a dedicated service identity for runtime dispatch. Approval operators should not have shell/root/database-write access as part of their normal role.

### Network and application firewall

- At infrastructure level, expose only required HTTPS ingress from approved source ranges or an identity-aware gateway. Keep SQLite local to the trusted host; never publish the DB port/file over the network.
- Restrict egress to explicitly required model/provider endpoints, identity provider, telemetry, and approved business APIs. Deny arbitrary destinations by default. Document exceptions and owners.
- Confirm the application ActionFirewall has explicit rules for every production tool/action and defaults to deny; a network firewall does not replace application authorization, and vice versa.
- Verify kill switch, tool allowlists, destination/argument validation, DNS/IP egress restrictions, rate limits, request-size limits, and audit logging. Ensure internal diagnostics and debug/docs endpoints are not publicly reachable unless approved.
- Record deployed image/commit SHA, config version, policy version, firewall ruleset hash, and change ticket. Do not store credentials or full sensitive payloads in this evidence.

## 8. Acceptance tests in the deployment environment

Run only in staging or an isolated production canary with a dedicated test tenant and harmless mock/canary tool. Do not test by sending real messages, transferring money, deleting user data, or invoking arbitrary production tools.

1. Configuration gate: confirm DB path is the intended absolute path, persistent and encrypted; real authenticator is registered (not noop/mock); policy includes exact approval permissions; firewall/egress rules are deployed. Save sanitized evidence only.
2. Authentication: GET approval listing without a token and with a random invalid token; both must return 401. Valid token must identify the expected principal.
3. RBAC: use test identities with no permission, list-only, decide-only, and resume-only grants. Confirm forbidden operations return 403 and allowed operations do not grant additional privileges.
4. Durability: create a canary approval, verify pending state, restart API/worker, and verify ID/status and original argument digest remain the same.
5. Decision: approve once as the authorized reviewer. Verify stored actor is the verified identity; duplicate or late decisions must not change the original decision.
6. Recovery: explicitly resume only the canary call. Verify current policy/firewall are rechecked, exact approved arguments are used, and repeated resume does not dispatch twice.
7. Crash/uncertainty: in staging, crash a worker after the canary provider records acceptance but before the outcome is persisted. After recovery, state must be uncertain; automatic dispatch count must remain one. Test provider-side idempotency if supported.
8. Network perimeter: from an untrusted network, verify API access is blocked or identity-gated; DB/storage are unreachable; unauthorized egress is denied; approved egress works. Capture firewall/security-group rule IDs and test results.
9. Backup/restore: create a canary approval, back up via SQLite online backup, encrypt, restore to an isolated path, run integrity/schema checks, verify canary state, and reconcile all claimed/uncertain dispatches before any worker starts.
10. Retention dry run: produce counts by status/age without printing payloads. Verify uncertain/pending records are excluded from any purge candidate set.
11. Negative tests: corrupt a copy of the DB and test missing/wrong decryption key. Both must fail closed without falling back to in-memory approvals or dispatching tools.
12. Sign-off: attach CI run, deployment SHA, sanitized config/policy/firewall identifiers, backup restore result, RPO/RTO, open risks, approver, and timestamp to the change record.

Acceptance passes only when every applicable check is evidenced in the actual target environment. Unit tests and GitHub CI are supporting evidence, not a substitute.

## 9. Reconciliation playbook for uncertain effects

For each uncertain row:
1. Freeze retries for that call ID and preserve approval evidence, argument digest, timestamps, and dispatch state.
2. Query the external provider's authoritative logs/events using provider idempotency key or correlation ID. Do not infer absence from a timeout or missing local response.
3. Classify the result:
   - Confirmed applied: record provider transaction/event ID and mark reconciled in the external incident/audit record. Do not dispatch again.
   - Confirmed not applied: only an authorized operator may initiate a controlled retry after revalidating the same approved call, deadline, policy, firewall, and provider idempotency semantics. Do not reset the existing consumed claim.
   - Cannot determine: leave state uncertain, escalate, and request business-owner decision. No automatic retry.
4. If retry is approved, create a new, explicitly linked operation/approval according to business policy; preserve old call ID and evidence. Reuse original provider idempotency key only when the provider explicitly guarantees the desired semantics.
5. Store decision, operator identity, evidence links, provider references, and timestamps in a tamper-evident audit/incident system. Never log secrets or full sensitive arguments.

## 10. Deployment record template

- Environment / region:
- Service version and Git commit:
- Change / incident ticket:
- Database path (no secrets):
- Encryption-at-rest provider and key ID/version (never key material):
- Backup target, encryption method, last verified backup:
- Restore drill date, RPO/RTO result:
- Authenticator implementation and identity-provider config version:
- Policy version and permissions tested:
- Firewall/security-group ruleset IDs and egress test results:
- Canary approval ID and test-tool reference:
- Uncertain dispatches and reconciliation records:
- Retention/legal-hold approval:
- Outstanding risks / exceptions / expiry date:
- Operator and independent approver:
