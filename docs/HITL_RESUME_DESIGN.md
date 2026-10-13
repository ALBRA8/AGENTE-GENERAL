# HITL suspension and resume: implementation contract

## Current behavior

The approval engine creates a `PENDING` request, but
`Agent._phase_check_permissions` immediately treats every non-`approved`
result as a denial. The execution loop then continues to the response phase.
Consequently, a later human decision changes the approval record but cannot
resume the original `Agent.run()` invocation.

The execution API stores only a redacted trace in `RequestContext._traces`.
It does not retain a resumable execution state, and that cache is process-local.
Adding an unauthenticated `approve` endpoint would turn the approval API into
a privilege-escalation path; this design must not do that.

## Implemented binding primitive (not a resume lifecycle)

Tool approval requests now retain the execution/correlation identity, agent and
session IDs, original tool-call ID, and a SHA-256 digest over canonical JSON
arguments. `ApprovalEngine.matches_approved_tool_call()` fails closed unless
the request is already approved and the action name, call ID, argument digest,
and execution context all match. Canonical key ordering is accepted; changed
argument values or a different execution are rejected.

The engine also exposes `claim_approved_tool_call()`, a synchronous
check-and-set that permits a bound approved call to be claimed only once per
execution within one process/event loop. It rejects pending, mismatched, and
past-deadline requests. This is process-local at-most-once protection only;
it does not survive restarts or coordinate multiple workers.

This helper is defense in depth for a future coordinator, not a dispatch
authorization by itself. The caller must still re-check current policy and
principal authorization. The approval engine remains in-memory, and the
runtime still lacks the authenticated suspend/resume coordinator described
below.

## Required lifecycle

1. **Create a bound approval.** Persist the approval ID together with the
   original execution ID, correlation ID, tool-call ID, tool name, canonical
   arguments (or their integrity digest), authenticated principal, and deadline.
   A decision must never be transferable to a different call or execution.
2. **Suspend explicitly.** Return an `awaiting_approval` execution state rather
   than converting `PENDING` into `DENY`. The pending execution must retain
   enough trusted state to continue at the permission gate without rerunning
   RECEIVE/REASON or regenerating the tool call.
3. **Authenticate the decider.** The decision endpoint must use the repository's
   verified identity/authentication mechanism and a policy that grants a specific
   approval permission. Caller-supplied `approver` strings and writable
   metadata are not proof of identity. If no authenticator/authorizer is
   configured, the endpoint must fail closed.
4. **Make decisions atomic and terminal.** Approval, rejection, expiry, and
   escalation must race through one synchronized state transition. Exactly one
   terminal decision may win; late decisions remain expired and are evidence-
   recorded. A duplicate request must return the existing terminal state without
   changing it.
5. **Revalidate before dispatch.** On approval, re-check the deadline, current
   policy, firewall/kill-switch, original call binding, verified principal,
   and execution identity. Issue a fresh short-lived authorization token for
   the exact original call. Never trust a serialized authorization verdict or
   token as a substitute for revalidation.
6. **Prevent replay.** Atomically claim the pending tool call before execution,
   keyed by execution ID and tool-call ID. A second resume attempt must not
   dispatch the side effect again, including after a timeout or duplicate HTTP
   request.
7. **Define durability.** In-memory suspension is only valid for a single
   process and cannot survive restart. A production deployment needs a durable
   execution/approval store and atomic compare-and-set semantics; otherwise
   the API must clearly expose the feature as process-local and non-durable.

## API and execution contract

The intended API shape is:

- `POST /api/v1/execution`: starts execution and may return
  `awaiting_approval` plus the execution ID and approval ID.
- `GET /api/v1/execution/{execution_id}`: reports the current state, including
  the pending approval reference, without exposing secrets or raw credentials.
- An authenticated decision operation: approves or rejects a specific approval
  request after checking the authenticated principal's permission. It must not
  accept a replacement tool call or replacement arguments from the decider.
- A resume worker/coordinator: claims the suspended execution, revalidates
  the original call, and resumes from the permission/execution boundary.

The decision operation should not be mounted until the application can enforce
authentication and authorization on it. Existing trace caching alone is not
a resumable execution store.

## Acceptance tests

- Pending HITL does not execute the tool and reports `awaiting_approval`.
- An authorized approval resumes the *same* execution and runs the bound call
  exactly once.
- Rejection, expiry, missing identity, insufficient role, changed arguments,
  changed execution ID, and expired deadline never execute the tool.
- Concurrent approve/reject and concurrent resume requests have one winner.
- Duplicate decisions and retries do not cause duplicate side effects.
- A restart either recovers the pending execution from durable state or reports
  that process-local state was lost; it must not silently reconstruct an
  untrusted execution from client-supplied data.
- Approval evidence records request, decision, expiry, and resume/dispatch
  outcomes in a verifiable chain.

## Implementation sequencing

1. Keep deadline enforcement and its regression tests isolated.
2. Introduce an explicit suspended-execution model and a single-process
   coordinator with atomic claim semantics, covered by unit tests.
3. Integrate the coordinator with the cognitive and legacy linear loops.
4. Add authenticated decision routes only after the app has a verified
   principal and explicit approval permission dependency.
5. Add durable storage and multi-worker concurrency guarantees before claiming
   production-grade resume support.
