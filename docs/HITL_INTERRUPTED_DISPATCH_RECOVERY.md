# HITL interrupted-dispatch recovery

## Purpose and limits

A durable HITL claim is committed before the external side effect is dispatched. If a worker exits before recording the dispatch result, the database cannot prove whether the provider performed the effect. The claim must remain non-retryable and its outcome must be represented as `unknown`.

`SQLiteSuspendedExecutionCoordinator.recover_interrupted_dispatches()` records `unknown` outcomes for claimed records that have no persisted outcome. It is idempotent: subsequent calls do not overwrite an existing outcome or make a claim retryable.

This is an operator reconciliation aid, **not** recovery of the original `Agent.run()` coroutine, provider state, or live authorization context. It does not provide exactly-once external effects.

## Required procedure

1. Stop or fence **every** worker and process that can dispatch the affected operations. Ensure an old worker cannot resume and perform a delayed side effect. Do not run recovery while dispatchers may still be active.
2. Open the same file-backed SQLite database used by the deployment, on a filesystem with supported SQLite locking semantics.
3. Run the recovery method and preserve its output as an operational record.
4. For each `unknown` result, reconcile against authoritative provider-side evidence (request IDs, provider logs, transaction status, or equivalent). Do not blindly retry an ambiguous operation.
5. Only after reconciliation establishes that retry is safe may an operator initiate a separately authorized retry under the product's idempotency/reconciliation policy. Do not reset the durable claim to achieve a retry.
6. Keep the process fenced until reconciliation is complete and any replacement worker is explicitly authorized to resume other work.

## Example

Run this only after completing step 1, using the deployment's actual database path:

```python
from albra_seed.approvals.sqlite_store import SQLiteSuspendedExecutionCoordinator

store = SQLiteSuspendedExecutionCoordinator("/path/to/hitl.sqlite3")
for outcome in store.recover_interrupted_dispatches():
    print(
        outcome.approval_id,
        outcome.outcome,
        outcome.observed_at.isoformat(),
        outcome.detail,
    )
```

Expected recovered outcomes use `outcome == "unknown"` and include
`reconcile before any retry` in the detail. An empty list means this call
did not find any unresolved claimed records; it does not prove that external
systems have no outstanding effects.

## Release limitations

- This procedure does not reconstruct a dead in-memory agent invocation.
- SQLite coordination is for workers sharing a local filesystem with SQLite locking semantics; do not assume an unverified network filesystem is safe.
- Database transactions cannot atomically commit arbitrary provider side effects. Use provider idempotency support and authoritative reconciliation where available.
- Validate this procedure in staging, including crash injection and backup/restore, before treating it as production-accepted.
