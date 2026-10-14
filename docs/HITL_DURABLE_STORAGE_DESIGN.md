# Human-in-the-loop approval security and durable recovery

## Implemented in this change

- The public API exposes `GET /api/v1/approvals/pending`,
  `POST /api/v1/approvals/{approval_id}/decision`, and an explicit
  `POST /api/v1/approvals/{approval_id}/resume` recovery operation.
- Recovery requires the exact `approval:resume` permission, a persisted
  APPROVED request, the saved original RunContext snapshot and argument digest,
  an unexpired deadline, and current policy/firewall approval. It does not
  invoke an LLM or accept replacement arguments from the caller.
- Recovery dispatches only the saved call and returns its tool result; it does
  not reconstruct the full conversational response or continue the original
  in-memory coroutine.
- Both endpoints require a Bearer credential verified by the registered
  `authenticator`, reject anonymous principals, and fail closed when
  authentication is not configured.
- Listing and deciding require explicit, action-specific policy rules whose
  required permissions are exactly `approval:list` and `approval:decide`.
  A generic ALLOW rule is not sufficient.
- Decision actor identity is taken from the verified principal, never from
  request JSON. Decision values and rejection reason length are validated.
- `SuspendedExecutionCoordinator` binds an approval to execution ID,
  correlation ID, tool-call ID/name, principal, deadline, and a SHA-256 digest
  of canonical arguments.
- `SQLiteSuspendedExecutionCoordinator` persists that binding without raw
  arguments or credentials, uses transactional state transitions, survives
  coordinator restart, and offers one-shot resume claims and dispatch-outcome
  records. Claims are never automatically reclaimed.
- Security tests cover missing/invalid credentials, missing explicit
  permissions, verified decider identity, duplicate decisions, SQLite restart,
  binding mismatches, deadline expiry, competing workers, and ambiguous
  dispatch outcomes.

## Important limits — not production-complete

The public approval API operates on the configured `ApprovalEngine`
storage. With `SQLiteApprovalStore`, approval records survive process restart
and a still-running waiter polls the durable record, so decisions made by a
different worker can be observed. The API does not become cross-worker safe
when each worker points at a different database file; workers must use the
same SQLite database on a filesystem with reliable locking semantics.

The `SQLiteSuspendedExecutionCoordinator` is a separate binding/claim
primitive and is **not yet wired into `ApprovalEngine` or the Agent runtime**.
It does not by itself resume tool execution.

A durable approval row does not serialize or reconstruct the suspended
`Agent.run()` coroutine, model/provider state, or live authorization context.
After a process restart, approval status can be recovered and inspected.
The original run is **not automatically resumed**; an authorized operator must
explicitly invoke the recovery endpoint for an approved call. Never recreate a
tool call from a model response or regenerate arguments during recovery; the
exact original call must be durably bound and revalidated before any dispatch
can be considered. In particular, the current `SQLiteApprovalStore` serializes
the `ApprovalRequest.context_ref`, including tool arguments and a RunContext
snapshot, to its database; treat that database as sensitive and restrict
filesystem access. The separate suspended-execution coordinator stores only
an arguments digest and remains a separate primitive.

SQLite can ensure a claim is committed once, but it cannot make an arbitrary
external side effect and a database commit atomic. If a worker dies after an
external service may have accepted an action, the outcome is uncertain. The
claim remains consumed and is not retried automatically. Use downstream
idempotency keys where supported and operator reconciliation otherwise. Do not
claim exactly-once effects for arbitrary tools.

## Deployment requirements

- Configure a real authenticator; the default Noop authenticator is anonymous
  and intentionally cannot approve.
- Add explicit, narrowly scoped policy rules for each approval permission.
- Use a local filesystem with reliable SQLite locking; do not assume an
  unverified network filesystem is safe.
- On POSIX, `SQLiteApprovalStore` creates the database with mode
  `0600` and rejects a symlink database path. Newly created parent directories
  use mode `0700`; operators must still verify permissions on pre-existing
  parent directories, WAL/SHM sidecars, backups, and deployment volumes.
- Before dispatch/resume, revalidate the exact call binding, deadline,
  current policy, firewall/kill-switch state, and trusted principal.
- Treat database errors, corrupt records, authorization failures, and
  ambiguous dispatch outcomes as fail-closed conditions.

## Remaining follow-up before production use

1. Consolidate the parallel `SQLiteApprovalStore` and
   `SQLiteSuspendedExecutionCoordinator` implementations behind a storage
   protocol, or document why both remain necessary. The recovery endpoint
   currently uses `SQLiteApprovalStore` and its dispatch ledger.
2. Define encryption/key-management and retention policies for the original
   tool arguments and RunContext snapshot. POSIX mode `0600` limits ordinary
   local access but does not protect data from a privileged host operator or
   stolen backups.
3. Decide whether the product needs full conversational continuation after a
   process death. The current recovery operation safely dispatches only the
   exact approved tool call and returns its result; it does not restore the
   dead coroutine, regenerate the final assistant response, or update every
   session/trace artifact.
4. Add process-level fault injection for the precise window where an external
   provider accepts a side effect but the worker dies before persisting its
   outcome. Verify that every recovery path treats the outcome as uncertain
   and never retries automatically. Continue testing corruption and missing
   evidence/records as explicit fail-closed cases.
5. Extend `SQLiteApprovalStore` schema migrations beyond the initial
   version-0-to-version-1 initialization and define a documented retention,
   backup, and restore procedure. Unknown schema versions now fail closed.
6. Keep running the full clean-room and security test suite on Python 3.10,
   3.11, and 3.12 for every change; no external tool can provide exactly-once
   guarantees without downstream idempotency support.
