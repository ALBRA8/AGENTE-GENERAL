# HITL restart recovery contract

**Status:** partial implementation. `SuspensionCheckpointV1` now defines a strict versioned payload, and the SQLite coordinator can persist/load it with a checksum and durable binding checks. This is not yet an end-to-end `Agent.run()` restore implementation.

## Safety boundary

A Python coroutine, model/provider session, in-memory tool registry, or live
authorization context must never be reconstructed from an approval row or from
client-supplied resume data. Restart recovery is permitted only from a
versioned, application-owned checkpoint written at an explicit safe suspension
boundary. The versioned payload and SQLite persistence primitives now exist,
but the runtime does not yet create them at the suspension boundary or restore
and continue `Agent.run()`. A pending or approved record whose owning execution
process has died remains **approval evidence**, not an automatically resumable execution.

## Checkpoint contract

A future checkpoint implementation must be versioned and validated before it
can resume. At minimum it must bind:

- execution ID and correlation ID;
- approval ID, tool-call ID, tool name, and canonical argument digest;
- stable principal subject/issuer binding (never bearer tokens or credentials);
- checkpoint schema version and runtime/agent compatibility identifier;
- suspension phase and a deterministic continuation point;
- creation time and original deadline.

The exact original tool arguments must be held in trusted server-side state or
reloaded from a trusted, integrity-checked checkpoint. The approver cannot
supply replacement arguments. A digest alone proves equality but cannot rebuild
arguments, so it is insufficient as the only checkpoint payload. The current
checkpoint checksum detects accidental corruption; it is not a cryptographic
MAC and does not protect against an attacker able to rewrite the database and
checksum together. Treat checkpoint arguments as potentially sensitive data:
use encrypted persistent storage and do not include credentials or reusable
tokens. Tool-specific safe-serialization review is required before production.

Before dispatch after restore, the service must authenticate the current caller
or worker identity, revalidate the principal binding, current policy/firewall
and kill-switch, deadline, tool availability, schema compatibility, and all
execution/call bindings. Any missing, corrupt, unknown-version, expired, or
incompatible checkpoint fails closed and requires operator reconciliation.

## State and crash semantics

| Observed state after restart | Required behavior |
|---|---|
| Awaiting approval, valid checkpoint | Rehydrate only the explicit checkpoint; continue waiting for a durable decision. |
| Approved, no dispatch claim | Restore only if the checkpoint validates and every authorization check is repeated; otherwise quarantine for reconciliation. |
| Claimed, no recorded outcome | Mark outcome `unknown` only after all possible dispatch workers are fenced. Never reset the claim or automatically retry. |
| Outcome `succeeded` | Treat as terminal; do not dispatch again. |
| Outcome `failed` or `unknown` | Do not infer retry safety. Require a tool-specific idempotency/reconciliation contract or operator action. |
| Expired, rejected, corrupt, or unknown schema | Fail closed; never dispatch. |

The SQLite coordinator's conservative handling of interrupted claims remains
necessary even after checkpoint restore is added. Exactly-once effects for
arbitrary external tools cannot be guaranteed by a local database transaction.

## Required tests before claiming restart-resume support

1. Spawn a worker, persist a checkpoint at the explicit suspension boundary,
   terminate that process, and start a fresh worker using a new coordinator.
2. Verify the new worker resumes the same execution/call without rerunning
   reasoning or regenerating arguments.
3. Change each binding independently (execution, correlation, principal,
   tool-call ID/name, argument digest) and assert no dispatch.
4. Test expired and unknown-version checkpoints, missing/corrupt checkpoint
   payloads, unavailable tools, and changed policy/firewall decisions.
5. Inject process termination before claim, after claim but before tool call,
   after the external effect but before outcome persistence, and after outcome
   persistence. Assert no automatic retry for ambiguous outcomes.
6. Race two fresh workers attempting restore/resume and prove a single durable
   claim wins.
7. Test migration/compatibility behavior for every supported checkpoint
   version and ensure old/unknown formats fail closed.

## Current acceptance statement

The checkpoint schema, strict payload validation, SQLite persistence, restart read, binding checks, runtime compatibility check, deadline check, and corruption detection have been added. The end-to-end restore implementation, explicit safe-boundary integration, trusted authorization/policy revalidation, and process-level fault-injection tests have not been completed. Until those pieces and tests pass,
the supported guarantee is durable approval/decision/claim evidence with
conservative no-retry behavior—not automatic recovery of the full
`Agent.run()` after process death. Keep issue #5 open and PR #3 in draft.
Real staging restart tests and backup/restore validation remain separate
acceptance requirements.
