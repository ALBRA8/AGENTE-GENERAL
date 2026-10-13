# Durable HITL storage and cross-worker resume design

Status: SQLite storage primitives, ApprovalEngine integration for durable approval decisions/polling/claims, API storage-scope reporting, runtime dispatch-outcome audit, and regression tests are implemented on the draft branch. On commit `6e85b4d7`, HITL Regression passed (934 passed, 14 skipped) and Clean-Room Verification passed on Python 3.10, 3.11, and 3.12 (1,005 passed, 1 skipped per interpreter). Full Agent.run recovery after the originating process dies is NOT implemented: the suspended coroutine and live authorization context are not serialized, and must not be reconstructed from an approval row alone.

## Goals and non-goals

Goals:
- Recover pending approval state after an API/worker restart.
- Serialize decisions and resume claims across independent processes.
- Keep approval identity bound to the original execution and exact tool call.
- Fail closed on storage, integrity, authorization, or deadline errors.

Non-goals:
- Claim universal exactly-once execution for arbitrary external tools. A process can crash after an external side effect succeeds but before its result is durably recorded. Exactly-once effects require tool-side idempotency or a transactional downstream system.

## Implemented in the current branch

`src/albra_seed/approvals/sqlite_store.py` adds a file-backed
`SQLiteSuspendedExecutionCoordinator` with durable bound approval records,
transactional terminal decisions, atomic one-shot resume claims, restart reads,
pending-record enumeration, polling for decisions made by another worker, and
a one-time dispatch-outcome audit record. It stores the canonical arguments
digest rather than raw tool arguments or credentials. SQLite storage errors
fail closed; a claim is never automatically reclaimed.

The tests in `tests/security/test_hitl_sqlite_store.py` cover coordinator
restart, independent-thread/process decision and claim races, deadline expiry,
corrupt storage, binding/authorization rejection, polling, and an ambiguous
dispatch crash window. `ApprovalEngine.record_dispatch_outcome()` and the
runtime execution boundary persist `succeeded` only on a successful tool result;
failures/exceptions are recorded as `unknown`, because a failed response does
not prove that an external side effect did not occur. The claim is not released.

**Remaining recovery boundary:** `ApprovalEngine` can be configured with
`suspension_store=SQLiteSuspendedExecutionCoordinator(path)`. With that
configuration, independent approval workers can read pending rows, commit a
decision, and observe/claim the decision using SQLite rather than a local
`asyncio.Event`. The HTTP pending-approvals response reports `durable_sqlite`
when this backend is configured. However, `Agent.run()` still holds the
suspended coroutine, provider/cognitive-loop state, and live authorization
context in process memory. If that process dies, the record survives but the
agent run is not restarted or resumed automatically. Production-grade restart
recovery and fault-injection testing remain outstanding.

## Storage boundary

Introduce a narrow storage protocol rather than coupling the runtime to SQL:
- `create_pending(record)`
- `get(approval_id)`
- `list_pending()`
- `decide(approval_id, expected_state, terminal_state, actor, reason, decided_at)`
- `claim_resume(approval_id, expected_binding, now, worker_id, lease_until)`
- `complete_dispatch(approval_id, claim_id, outcome_reference)`
- `release_or_expire_claim(approval_id, claim_id, reason)`

Every state-changing method must be atomic. The in-memory implementation remains useful for unit tests and local development. A SQLite implementation should use transactions and conditional updates, not read-then-write logic in Python.

## Durable records

Persist at minimum:
- approval ID, execution ID, correlation ID, agent/session IDs;
- tool-call ID, tool name, canonical argument digest (and the original validated call payload when needed to replay the exact call);
- stable authenticated principal identity, kind, roles snapshot for audit, and authentication issuer/tenant namespace where available;
- created/deadline timestamps, current state, terminal decision, verified decider identity, reason;
- resume claim ID, claim owner/lease, dispatch attempt count, durable outcome reference;
- schema version and integrity/audit metadata.

Never persist bearer credentials or reusable authorization tokens. On resume, obtain fresh credentials through the configured trusted mechanism or require a documented service identity, then revalidate current policy and firewall rules.

## SQLite transaction model

Use a unique primary key on approval ID and unique constraints for the bound execution/tool-call identity. For a decision, execute a conditional update such as:

```sql
UPDATE approvals
SET state = ?, decided_by = ?, reason = ?, decided_at = ?
WHERE approval_id = ? AND state = 'PENDING' AND deadline > ?;
```

Exactly one row must change. Zero rows means the approval is missing, expired, or already terminal; read back the record to return the existing safe state without overwriting it.

For a resume claim, similarly condition on approved state, unexpired deadline, matching binding digest, and no completed/active claim. Use a durable unique claim ID. A lease may permit recovery from a dead worker, but reclaiming a lease must not blindly repeat a side effect whose outcome is unknown.

SQLite should be configured with foreign keys, a documented journal/synchronous policy, bounded busy timeout, and restrictive filesystem permissions. Storage errors are denial/errors, never permission to continue. Add schema versioning and an explicit migration path.

## Notifications and recovery

In-process asyncio Events may remain an optimization but must not be authoritative. A waiter should re-read durable state on wakeup and poll with a bounded interval so decisions from another process are observed. At startup, reconcile pending records:
- expired pending records transition to EXPIRED atomically;
- approved records without a claim may be resumed after revalidation;
- claims with expired leases and unknown dispatch outcome enter a recoverable/uncertain state unless tool idempotency makes retry safe;
- corrupt or binding-inconsistent records are quarantined and never dispatched.

## Side-effect safety

A database transaction cannot atomically commit with arbitrary external tool side effects. The implementation must distinguish:
1. approved but not claimed;
2. claimed but dispatch not confirmed;
3. dispatch outcome durably recorded.

Pass an idempotency key derived from the durable claim to adapters that explicitly support it. For tools without idempotency support, an ambiguous crash after dispatch must not be automatically retried; expose an operator-reconciliation state instead. Document this limitation in API responses and operational guidance.

## Authorization and audit

Each decision request still requires a verified non-anonymous principal, an explicit `approval:decide` grant, and current authorization. Caller-provided approver IDs and replacement arguments are not trusted. Before dispatch, re-check the original binding, deadline, current principal constraints, policy, and firewall/kill-switch. Store a tamper-evident decision/dispatch audit record in the same transaction where feasible.

## Required tests

- Two independent store/coordinator instances racing to decide: one terminal decision wins.
- Two workers racing to claim resume: one valid claim wins.
- Restart recovers pending/approved state without rerunning reasoning or changing arguments.
- Expired approvals and expired claims fail closed.
- Corrupt records, schema mismatch, locked/unavailable DB, and failed transactions never dispatch.
- Crash simulation before dispatch, after dispatch, and before outcome persistence distinguishes safe retry from uncertain outcome.
- Unsupported idempotency tools are never automatically retried after ambiguous dispatch.
- Current authorization is revalidated on resume, not trusted from a stored verdict.

## Delivery sequence

1. Land protocol and conformance tests while keeping the current in-memory backend behavior.
2. Implement SQLite schema/migrations and transactional CAS tests.
3. [Implemented, partial recovery only] Wire configurable SQLite storage into ApprovalEngine; expose cross-worker decisions, polling, atomic claims, and API storage scope.
4. [Implemented primitives] Add pending-record recovery/polling and one-shot worker claims; never reclaim ambiguous dispatch claims automatically.
5. [Tests passed for current coordinator boundary] Crash-window/no-reclaim, independent OS-process claims, and the complete Python 3.10–3.12 Clean-Room matrix pass; end-to-end Agent.run restart/fault-injection tests remain outstanding.
6. [Partial] Expose configured backend scope; end-to-end process restart recovery and operator reconciliation API remain outstanding.

Related: issue #5; draft PR #3 contains the current process-local lifecycle.
