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
- Protect the database and its parent directory with restrictive OS
  permissions and backups appropriate to the deployment.
- Before dispatch/resume, revalidate the exact call binding, deadline,
  current policy, firewall/kill-switch state, and trusted principal.
- Treat database errors, corrupt records, authorization failures, and
  ambiguous dispatch outcomes as fail-closed conditions.

## Required follow-up before production use

1. Define a storage protocol and integrate the SQLite coordinator into
   `ApprovalEngine` without changing existing in-memory behavior.
2. Persist the original validated tool-call payload securely if replay requires
   it; persist only a digest when the payload must not be retained.
3. Add a recovery protocol that reconstitutes only the original validated
   execution state, obtains fresh trusted authorization, and never reruns
   reasoning to regenerate a pending action.
4. Add fault-injection tests for crashes before dispatch, after dispatch, and
   before recording the outcome; verify that unknown outcomes are never
   automatically retried.
5. Run the complete suite on Python 3.10, 3.11, and 3.12 and merge only after
   all required checks pass.
