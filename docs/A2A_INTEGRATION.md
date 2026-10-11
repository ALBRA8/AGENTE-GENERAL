# A2A Integration — Agent-to-Agent Protocol Adapter

This document describes how ALBRA AGENT SEED V1.0 integrates the **A2A
(Agent2Agent) protocol** as a universal, domain-agnostic interop layer on
top of the existing Handoff / Security / Audit / Evidence / Provenance
stack. A2A is an **optional translation layer**: it lets an ALBRA agent
talk to *other* ALBRA agents — or any A2A-compliant external agent —
without duplicating any of the seed's core machinery.

> **Single source of truth:** the `AgentManifest` is the canonical
> identity; the A2A `AgentCard` is *derived* from it. The Handoff
> `HandoffRequest` / `HandoffResponse` is the canonical execution
> contract; the A2A `A2ATask` is *translated* into and out of it.

---

## 1. Architecture

A2A lives at the same level as MCP and Handoff — three integration
channels feeding into the same shared execution / evidence / provenance /
audit spine:

> **Canonical certification record:** [`docs/CERTIFICATION.md`](CERTIFICATION.md).
> A2A test counts (72 across 4 files) and overall counts (444) are mirrored
> from that file.

```
                            ┌─────────────────────────────────────────┐
                            │            ALBRA AGENT (seed)            │
                            │                                         └──┐
                            │   ┌────────────┐  ┌──────────────────┐    │ │
       external tools ──────┼──▶│    MCP     │  │     Handoff       │◄──┼─┼── internal
                            │   │ (tool bus) │  │  (ALBRA↔ALBRA)    │    │ │  ALBRA agents
                            │   └─────┬──────┘  └─────────┬────────┘    │ │
                            │         │                   │             │ │
                            │   ┌─────┴───────────────────┴──────────┐  │ │
                            │   │     A2A (external agent interop)    │◄─┼─┘ external
                            │   │     AgentCard · A2ATask · Transport │  │  A2A agents
                            │   └─────────────────┬───────────────────┘  │
                            │                     │                      │
                            │   ┌─────────────────▼───────────────────┐  │
                            │   │   SHARED SPINE (never bypassed)      │  │
                            │   │  Execution · Evidence · Provenance ·  │  │
                            │   │  Audit · Security · Policy · HITL    │  │
                            │   └─────────────────────────────────────┘  │
                            └─────────────────────────────────────────────┘
                                                ▲
                                                │
                                  identical stack — local or remote
```

**Three channels, one spine.** Whether a request arrives via MCP, via
internal Handoff, or via remote A2A, the same Execution runner produces
the outcome, the same Evidence collector registers proof, the same
Provenance tracker chains the parent/child execution, and the same
Audit log captures the entry.

---

## 2. Relationship: MCP / Handoff / A2A

| Aspect            | MCP                                   | Handoff                                  | A2A                                          |
|-------------------|---------------------------------------|------------------------------------------|----------------------------------------------|
| **Purpose**       | Universal tool bus                    | ALBRA-to-ALBRA agent delegation          | Cross-vendor agent interoperability           |
| **Scope**         | Tool invocation                      | Whole-task delegation to another agent    | Whole-task delegation to any compliant agent  |
| **Wire format**   | MCP JSON-RPC                          | `HandoffRequest` / `HandoffResponse`     | A2A `A2ATask` / `A2AMessage` / `A2AArtifact`  |
| **Peers**         | Tool servers                         | ALBRA agents only                        | Any A2A-compliant agent (incl. ALBRA)         |
| **Identity**      | Tool descriptors                     | `AgentIdentity` (full)                   | `AgentCard` (derived subset)                  |
| **Transport**     | stdio / HTTP                         | In-process (HandoffManager)              | `InProcessTransport` or `HTTPTransport`        |
| **Network needed?**| Optional                            | No                                       | No (CORE_ONLY uses InProcessTransport)        |
| **Audit action**  | `mcp.tool.invoke`                    | `handoff.receive`                        | `a2a.task.receive`                             |
| **Goes through Security stack?** | Yes                      | Yes                                      | Yes (identical pipeline)                      |
| **Reuses Evidence / Provenance?** | Yes                     | Yes                                      | Yes                                           |

A2A is the **widest** channel (any compliant peer) but the **thinnest**
contract — it only carries what the A2A protocol defines, and relies
entirely on the shared spine for everything else.

---

## 3. Agent Card

The `AgentCard` is the A2A discovery document. It is **never** a
parallel source of truth — every identity field is *derived* from the
canonical `AgentManifest` via `manifest_to_card()`.

### 3.1 Field mapping — `AgentManifest` → `AgentCard`

| `AgentCard` field       | Source (manifest)                          | Notes                                                                 |
|-------------------------|--------------------------------------------|-----------------------------------------------------------------------|
| `protocol_version`      | constant `"1.0"`                           | A2A protocol version pinned                                          |
| `name`                  | `identity.name`                            | Agent's display name                                                 |
| `description`           | argument or auto `f"ALBRA agent: {name}"`  | May be overridden by specialization                                 |
| `version`               | `identity.version`                         | Agent version (not protocol version)                                |
| `endpoint`              | arg or `identity.endpoint`                  | Optional — `None` in CORE_ONLY mode                                 |
| `capabilities`          | `identity.capabilities`                    | Verbatim list                                                        |
| `skills`                | `identity.skills` (derived to `A2ACardSkill`) | Each skill name becomes an `A2ACardSkill(id=name, name=name)`       |
| `default_input_modes`   | `identity.interfaces` or `["text"]`        | Defaults to text                                                    |
| `default_output_modes`  | `["text"]`                                 | v1 ships text-only output                                            |
| `authentication`        | arg `authentication_schemes`               | Empty dict by default (CORE_ONLY = no auth)                         |
| `provider`              | built from identity providers + versions   | Includes `seed_version`, `runtime_version`, `specialization`        |
| `metadata.agent_id`     | `identity.agent_id`                        | Stable agent identifier                                             |
| `metadata.autonomy_level` | `identity.autonomy_level.name`           | e.g. `L0_OBSERVE`                                                    |
| `metadata.risk_profile` | `identity.risk_profile.value`              | e.g. `low`                                                           |
| `metadata.manifest_version` | `manifest.manifest_version`            | Manifest schema version                                             |
| `generated_at`          | `utc_now().isoformat()`                    | Card generation timestamp                                            |

### 3.2 Usage

```python
from albra_seed.a2a import manifest_to_card
from albra_seed.core.identity import AgentIdentity, AgentManifest

identity = AgentIdentity(name="agent-legal", version="1.2.0", specialization="legal")
manifest = AgentManifest(identity=identity)

card = manifest_to_card(manifest, endpoint="https://agent.example/a2a")
print(card.model_dump_json(indent=2))
```

Specializations may extend the card with extra skills via the
`extra_skills` argument; existing skills (by `id`) are never overwritten
by extras — extras are additive only.

---

## 4. A2A ↔ ALBRA Mapping

| A2A concept          | ALBRA concept                | Reused or added? | Notes                                                  |
|----------------------|------------------------------|------------------|--------------------------------------------------------|
| `A2ATask.task_id`    | `HandoffRequest.execution_id`| REUSED (same value)| Task ID == execution ID; full provenance chain intact  |
| `A2ATask.message`    | `HandoffRequest.objective`  | ADDED            | A2A's `Message.text` becomes the objective string     |
| `A2ATask.artifacts`  | `HandoffResponse.result`    | ADDED            | Response result is wrapped into one `A2AArtifact`      |
| `A2ATask.state`      | `HandoffResponse.status`    | ADDED            | Mapped via `_STATUS_MAP` (see §5)                     |
| `A2ATask.requester`  | `HandoffRequest.requester`  | REUSED           | Same field, same semantics                            |
| `A2ATask.target`     | `HandoffRequest.target`     | REUSED           | Same field, same semantics                            |
| `A2ATask.correlation_id`   | `HandoffRequest.correlation_id`      | REUSED | Propagates end-to-end into Audit                     |
| `A2ATask.parent_execution_id` | `HandoffRequest.parent_execution_id` | REUSED | Propagates into Provenance chain                  |
| `A2ATask.deadline`   | `HandoffRequest.deadline`   | REUSED           | Same datetime semantics                               |
| `A2ATask.metadata`   | `HandoffRequest.input["a2a_metadata"]` + `metadata` | ADDED | A2A metadata surfaces into handoff input payload |
| `HandoffResponse.confidence` | `A2ATask.metadata["confidence"]` | ADDED | Surfaced in metadata, not in card                    |
| `HandoffResponse.warnings` | `A2ATask.metadata["warnings"]`     | ADDED | Surfaced in metadata                                 |
| `HandoffResponse.errors`   | `A2ATask.metadata["errors"]`       | ADDED | Surfaced in metadata                                 |
| `HandoffResponse.evidence` | `A2ATask.metadata["evidence"]`    | ADDED | Evidence list also registered via EvidenceManager   |

**What's reused:** every execution-side concept (execution_id,
correlation_id, parent chain, deadline, requester/target identity).
**What's added:** the A2A wire shapes (`A2AMessage`, `A2AArtifact`,
`A2ATask`) and the `TaskState` lifecycle vocabulary.

---

## 5. Task Lifecycle

A2A defines a six-state task lifecycle. ALBRA's `HandoffStatus` has
eight values; the adapter collapses them into the A2A states via a
deterministic `_STATUS_MAP`:

```
                          ┌───────────────┐
                          │   SUBMITTED   │ ◄── A2AClient.send_task
                          └───────┬───────┘
                                  │  A2AServer.receive()
                                  │  (security passes)
                                  ▼
                          ┌───────────────┐
                          │    WORKING    │ ◄── HandoffManager dispatch
                          └──┬────┬───┬───┘
                             │    │   │
              require input  │    │   │  cancelled
              (HITL pending) │    │   │
                             ▼    │   ▼
                ┌─────────────────┐│ ┌───────────┐
                │ INPUT_REQUIRED  ││ │  CANCELED │
                └────────┬────────┘│ └───────────┘
                         │         │
                  input received   │
                         │         ▼
                         │  ┌───────────┐
                         │  │  FAILED   │ ◄── REJECTED, TIMEOUT, exception
                         │  └───────────┘
                         ▼
                  ┌─────────────┐
                  │  COMPLETED  │ ◄── SUCCEEDED
                  └─────────────┘
                       (terminal)
```

### Status mapping table

| Handoff status      | A2A `TaskState` | Terminal? |
|---------------------|-----------------|-----------|
| `pending`           | `SUBMITTED`     | no        |
| `accepted`          | `WORKING`       | no        |
| `in_progress`       | `WORKING`       | no        |
| `succeeded`         | `COMPLETED`     | **yes**   |
| `failed`            | `FAILED`        | **yes**   |
| `rejected`          | `FAILED`        | **yes**   |
| `timeout`           | `FAILED`        | **yes**   |
| `cancelled`         | `CANCELED`      | **yes**   |
| *(unknown)*         | `FAILED`        | **yes**   |

> **Defensive default:** an unknown Handoff status maps to `FAILED`,
> never to `COMPLETED`. The adapter never lies about a successful
> outcome.

---

## 6. Security Pipeline

The A2A path **never bypasses** the existing security stack. Every
inbound task walks the same pipeline as a local handoff — A2A simply
adds the auth extraction step at the front and reuses everything else.

```
   A2A request (A2ATask + optional token)
        │
        ▼
   1. AUTHENTICATE      ── runtime.authenticator (NoopAuthenticator default → anonymous)
        │                  Real Authenticator + bad token → DENY
        ▼
   2. AUTHORIZE         ── (composed by firewall + policy; principal.roles surfaced)
        │
        ▼
   3. FIREWALL          ── ActionFirewall.verdict("a2a.task.receive", actor, ctx)
        │                  DEFAULT-DENY → unknown actions blocked
        │                  Lazy: skipped if no firewall registered (CORE_ONLY)
        ▼
   4. POLICY            ── PolicyEngine.evaluate(action, ctx, roles, actor_id)
        │                  DENY          → task FAILED, audit "policy_denied"
        │                  REQUIRE_HITL  → task SUBMITTED + pending_approval_id
        │                  ALLOW/DRY_RUN → proceed
        ▼
   5. RISK + AUTONOMY   ── (already composed inside policy_engine)
        │
        ▼
   6. HITL (if required) ── ApprovalEngine.request(action, ctx, actor)
        │                  Returns approval_id; task parked until approval granted
        ▼
   7. HANDOFF DISPATCH  ── A2AAdapter.task_to_handoff_request → HandoffManager.receive
        │
        ▼
   8. AUDIT + EVIDENCE + PROVENANCE  ── always recorded, even on denial
```

### Key invariants

- **Never bypassed.** Even fully in-process A2A requests go through the
  same `A2ASecurityFilter.evaluate()` call.
- **Lazy components.** If `firewall` / `policy_engine` / `approval_engine`
  are not registered (CORE_ONLY), the corresponding step is *skipped* —
  the filter never raises for a missing component.
- **Default-deny firewall.** When an `ActionFirewall` IS registered,
  the canonical action `a2a.task.receive` must be explicitly allowed
  or the task is denied.
- **Audit on every outcome.** Denied, failed, and succeeded tasks all
  produce an audit entry; provenance and evidence are tracked for every
  received task (input provenance runs *before* the security check).

---

## 7. Tracing & Provenance

Three identifiers propagate from the A2A envelope through Handoff into
Execution, Audit, Provenance and Evidence:

| ID                       | Origin                          | Where it surfaces                                     |
|--------------------------|---------------------------------|-------------------------------------------------------|
| `execution_id`           | `A2ATask.task_id`               | HandoffRequest.execution_id → ExecutionTrace → Audit → Evidence |
| `correlation_id`         | `A2ATask.correlation_id`        | HandoffRequest.correlation_id → Audit `correlation_id` field |
| `parent_execution_id`    | `A2ATask.parent_execution_id`   | HandoffRequest.parent_execution_id → ProvenanceChain parent node |

```
   A2A envelope
   ┌──────────────────────────────────────┐
   │ task_id            = X              │
   │ correlation_id     = C              │
   │ parent_execution_id = P (optional) │
   └────────────────┬─────────────────────┘
                   │ A2AAdapter.task_to_handoff_request
                   ▼
   HandoffRequest
   ┌──────────────────────────────────────┐
   │ execution_id        = X             │  ◄── same value as task_id
   │ correlation_id      = C             │
   │ parent_execution_id = P             │
   └────────────────┬─────────────────────┘
                   │ HandoffManager.receive → handler
                   ▼
   Execution / Evidence / Provenance / Audit
   ┌──────────────────────────────────────┐
   │ execution_id        = X             │
   │ correlation_id      = C             │
   │ parent_execution_id = P             │
   └──────────────────────────────────────┘
```

Because `task_id` is reused as `execution_id`, the entire downstream
chain — ExecutionTrace, Evidence records, Provenance nodes, Audit
entries — is reachable from a single A2A task ID.

---

## 8. Transport

Two implementations of the `A2ATransport` ABC share the same async
API (`send`, `get_status`, `cancel`, `health`):

### 8.1 `InProcessTransport` — ALWAYS available

Routes calls directly to a registered `A2AServer` via a dict lookup.
No sockets, no serialization beyond model copying. Used in CORE_ONLY
mode, in tests, and in single-process multi-agent topologies.

### 8.2 `HTTPTransport` — lazy-imports `httpx`

`httpx` is imported in `__init__`. If it is not installed, instantiating
`HTTPTransport` raises `NotImplementedError`. The seed remains
CORE_ONLY — `httpx` is an **optional** dependency.

> **v1 status:** `HTTPTransport.__init__` correctly verifies `httpx`
> is installed, but the actual `send` / `get_status` / `cancel` methods
> raise `NotImplementedError` in v1. See §10.

### 8.3 CORE_ONLY guarantee

**Network is NEVER required for birth.** The A2A subpackage is
lazy-imported from the top-level `albra_seed` package via
`__getattr__`, so `import albra_seed` does not pull in `a2a` at all.
Even when `a2a` IS imported, the `InProcessTransport` requires no
external packages — the full `A2AClient → A2AServer → HandoffManager`
round-trip works on a fresh clone with zero optional deps.

---

## 9. Supported Operations (v1)

| Operation                                   | Status            | Entry point                                          |
|--------------------------------------------|-------------------|------------------------------------------------------|
| Generate Agent Card from manifest           | **IMPLEMENTED**   | `manifest_to_card(manifest, endpoint=…)`             |
| Register agent in directory                 | **IMPLEMENTED**   | `A2ARegistry.register(record)`                       |
| Lookup agent by id / endpoint              | **IMPLEMENTED**   | `A2ARegistry.get(agent_id)`, `.get_by_endpoint(url)` |
| Send task (in-process)                      | **IMPLEMENTED**   | `A2AClient.send_task(url, message=…)`                |
| Receive task (in-process)                  | **IMPLEMENTED**   | `A2AServer.receive(task, token=…)`                   |
| Status query                               | **IMPLEMENTED**   | `A2AClient.get_status(url, task_id)` / `A2AServer.get_status(id)` |
| Cancel task                                | **IMPLEMENTED**   | `A2AClient.cancel(url, task_id)` / `A2AServer.cancel(id)` |
| Artifacts (text + data parts)              | **IMPLEMENTED**   | `A2AArtifact.from_text(…)` / `_result_to_artifact`   |
| Idempotency (terminal task resubmit)       | **IMPLEMENTED**   | `A2AServer.receive` returns stored result on resubmit |
| Deadline enforcement                       | **IMPLEMENTED**   | `A2AServer.receive` checks `task.deadline`           |
| Malformed-task rejection                    | **IMPLEMENTED**   | `A2AServer.receive` checks `message is None`         |
| Security pipeline (auth → firewall → policy → HITL) | **IMPLEMENTED** | `A2ASecurityFilter.evaluate`                  |
| Audit / Evidence / Provenance propagation  | **IMPLEMENTED**   | `a2a.observers` (lazy, no-op if absent)              |
| InProcessTransport                         | **IMPLEMENTED**   | `InProcessTransport`                                 |
| `send_with_timeout` convenience            | **IMPLEMENTED**   | `A2AClient.send_with_timeout(url, message, timeout)` |

---

## 10. Unsupported Operations (deferred to v2)

The following A2A protocol features are **NOT IMPLEMENTED** in v1.
Honest reporting — no silent stubs that pretend to work:

| Operation                                   | Status                | Why deferred                                       |
|--------------------------------------------|-----------------------|----------------------------------------------------|
| Streaming responses (SSE / chunked)         | **NOT_IMPLEMENTED**   | Async iterator protocol not wired; v2 will add `send_streaming` |
| Push notifications (webhook subscription)  | **NOT_IMPLEMENTED**   | Requires webhook registration endpoint + retry    |
| Multimodal parts beyond `text`             | **PARTIAL**           | `A2AMessage`/`A2AArtifact` accept arbitrary parts dicts; only `text` and `data` parts are produced by the adapter. `file`, `image`, `audio` pass-through is not validated. |
| Multiple artifacts per task                | **PARTIAL**           | Adapter appends one artifact per response; clients may set more on the input but the adapter does not enrich them |
| Role-based authorization (RBAC) step       | **NOT_IMPLEMENTED**   | Composed informally via `principal.roles` flowing into PolicyEngine; no dedicated RBAC layer |
| Per-task progress events                  | **NOT_IMPLEMENTED**   | v1 returns final state only                         |

The HTTP transport (`HTTPTransport.send` / `get_status` / `cancel` /
`discover`) and the well-known discovery route (`/.well-known/agent-card.json`)
were IMPLEMENTED in the A2A-GAP-A + A2A-GAP-B passes (see `tests/a2a/test_a2a_wire.py`,
`tests/a2a/test_a2a_routes.py`, `tests/a2a/test_external_interop.py`).

---

## 11. CORE_ONLY Birth Guarantee

The seed MUST be born without network. A2A respects this:

| Concern                              | CORE_ONLY behaviour                                              |
|--------------------------------------|------------------------------------------------------------------|
| `import albra_seed`                  | Does NOT import `a2a` (lazy via `__getattr__`)                   |
| `from albra_seed.a2a import …`       | Works without `httpx`, `fastapi`, `uvicorn`, etc.                |
| `InProcessTransport()`               | Always constructible, no external deps                           |
| `A2AServer(...)`                     | Constructible with a CORE_ONLY runtime (all security components absent → all skipped) |
| `A2AClient(InProcessTransport())`    | Full send → receive → status → cancel round-trip works           |
| `HTTPTransport()`                    | Raises `NotImplementedError` if `httpx` missing (intentional)   |
| `manifest_to_card(manifest)`         | Returns a valid `AgentCard` with `endpoint=None` by default      |

The `tests/a2a/test_a2a.py::test_core_only_without_network` test
verifies this: an `A2AServer` constructed with a CORE_ONLY runtime
(firewall=None, policy_engine=None, approval_engine=None,
audit_logger=None) still successfully receives, dispatches, and
returns a task with `state=COMPLETED`.

---

## 12. Testing

**72 A2A tests** across **4 files**. All pass in <1s with no external
network (real HTTP is exercised via `httpx.MockTransport` and
`httpx.ASGITransport`). Counts reproduced from
[`docs/CERTIFICATION.md`](CERTIFICATION.md).

| File                                  | Tests | Coverage                                                                 |
| ------------------------------------- | ----- | ------------------------------------------------------------------------ |
| `tests/a2a/test_a2a.py`                | 28    | Card mapping, translation, lifecycle, security, tracing, transport, error handling, idempotency, artifacts, registry |
| `tests/a2a/test_a2a_wire.py`           | 16    | A2A 1.0 wire-format serialization (`agent_card_to_wire`, `task_to_wire`, JSON-RPC envelopes) + real HTTP transport via `httpx.MockTransport` |
| `tests/a2a/test_a2a_routes.py`         | 10    | FastAPI routes via `TestClient`: `/.well-known/agent-card.json` + `/a2a/v1/` JSON-RPC dispatch (message/send, tasks/get, tasks/cancel, unknown method -32601, malformed body, Authorization header propagation, no-server 404) |
| `tests/a2a/test_external_interop.py`   | 18    | ALBRA ↔ non-ALBRA reference agent (standalone FastAPI fixture with ZERO `albra_seed` imports) via real HTTP over `httpx.ASGITransport`. Covers discovery, send, get_status, cancel in BOTH directions + ALBRA↔ALBRA end-to-end + security propagation + missing-httpx fallback |
| **Total A2A**                          | **72** |                                                                          |

Per-category breakdown of `tests/a2a/test_a2a.py` (28 tests):

| Test category            | Tests (count) | Representative tests                                                   |
|--------------------------|---------------|------------------------------------------------------------------------|
| Card mapping             | 3             | `test_agent_card_generation_from_manifest`, `test_manifest_to_card_mapping_preserves_identity`, `test_a2a_card_endpoint_field` |
| Translation (A2A↔Handoff)| 2             | `test_a2a_request_to_handoff_request_translation`, `test_handoff_response_to_a2a_response_translation` |
| Lifecycle                | 3             | `test_task_lifecycle_submitted_to_completed`, `test_task_status_query`, `test_task_cancellation` |
| Security                 | 6             | `test_permission_denied_when_unauthorized`, `test_firewall_blocks_when_no_matching_rule`, `test_policy_engine_returns_require_hitl`, `test_hitl_enforcement_pending_approval`, `test_unauthorized_a2a_request_rejected`, `test_risk_classification_propagates` |
| Tracing & provenance     | 4             | `test_correlation_id_propagation`, `test_provenance_attached_to_a2a_request`, `test_evidence_attached_to_a2a_response`, `test_audit_log_captures_a2a_operation` |
| Transport                | 3             | `test_in_process_transport_routes_to_server`, `test_core_only_without_network`, `test_a2a_http_transport_lazy_import` + `test_a2a_http_transport_methods_work_with_httpx` |
| Error handling           | 3             | `test_malformed_a2a_request_rejected`, `test_a2a_timeout`, `test_a2a_failure_propagation` |
| Idempotency              | 1             | `test_a2a_idempotency`                                                 |
| Artifacts                | 1             | `test_artifacts_propagation`                                            |
| Registry                 | 1             | `test_a2a_registry_register_and_lookup`                                 |

**Full-suite impact:** **444 total tests pass** (was 372 after GAP-1..7
+ 28 A2A-IMPL + 26 A2A-GAP-A + 18 A2A-GAP-B = 444; 0 regressions).

---

## 13. Examples

### 13.1 Generate an AgentCard

```python
from albra_seed.a2a import manifest_to_card, A2ACardSkill
from albra_seed.core.identity import AgentIdentity, AgentManifest

identity = AgentIdentity(
    name="agent-research",
    version="1.0.0",
    capabilities=["reason", "search"],
    skills=["literature_review", "summarize"],
    specialization="research",
)
manifest = AgentManifest(identity=identity)

card = manifest_to_card(
    manifest,
    endpoint="https://agent.example/a2a",
    description="Research assistant agent",
    authentication_schemes=[{"type": "bearer"}],
    extra_skills=[A2ACardSkill(id="cite", name="cite", tags=["academic"])],
)
```

### 13.2 Stand up an A2AServer (in-process)

```python
import asyncio
from albra_seed.a2a import A2AServer, InProcessTransport, A2AClient
from albra_seed.core.identity import AgentIdentity
from albra_seed.handoff.contract import HandoffRequest, HandoffResponse, HandoffStatus
from albra_seed.handoff.manager import HandoffManager
from albra_seed.runtime.runtime import Runtime

runtime = Runtime()  # CORE_ONLY: no security components registered
identity = AgentIdentity(name="worker-1")
handoff_mgr = HandoffManager()

async def my_handler(req: HandoffRequest) -> HandoffResponse:
    return HandoffResponse.success(
        req.execution_id,
        result={"answer": f"processed: {req.objective}"},
        confidence=0.9,
        responder=req.target,
    )

server = A2AServer(
    runtime,
    identity=identity,
    handoff_manager=handoff_mgr,
    handler=my_handler,
)

transport = InProcessTransport()
transport.register("agent://worker-1", server)
client = A2AClient(transport, requester="caller")
```

### 13.3 Send a task via InProcessTransport

```python
async def main():
    task = await client.send_task(
        "agent://worker-1",
        message="Summarize the attached paper",
        metadata={"risk": "low"},
    )
    print(task.state)        # TaskState.COMPLETED
    print(task.artifacts[0].parts[0]["text"])  # processed: ...

asyncio.run(main())
```

### 13.4 Query status

```python
status = await client.get_status("agent://worker-1", task.task_id)
# A2ATaskStatus(task_id=…, state=COMPLETED, artifacts=[…], updated_at=…)
```

### 13.5 Cancel a task

```python
final = await client.cancel("agent://worker-1", task.task_id)
if final is not None:
    assert final.state == TaskState.CANCELED
```

### 13.6 Expose the AgentCard

```python
card = server.get_agent_card(endpoint="agent://worker-1")
# card.model_dump_json() — serialize and serve over your transport of choice
```

---

## 14. Limitations (honest list)

What v1 deliberately does NOT do:

1. **No real HTTP transport.** `HTTPTransport` validates `httpx` is
   installed but the `send` / `get_status` / `cancel` methods raise
   `NotImplementedError`. Use `InProcessTransport` only.
2. **No streaming.** A2A streaming responses (SSE / chunked) are not
   wired; the API returns the final task synchronously.
3. **No push notifications.** No webhook registration, no async
   delivery of completion events.
4. **Multimodal parts are pass-through only.** The adapter produces
   only `text` and `data` parts. `file`, `image`, `audio` parts in
   inbound messages are not validated or transformed; they ride in
   `metadata` only.
5. **No external discovery.** No `/.well-known/agent-card` HTTP
   endpoint; discovery is via the in-process `A2ARegistry` only.
6. **No persistent task store.** `A2AServer._tasks` is a plain dict;
   server restart loses task history. (Audit / Evidence / Provenance
   records DO persist via their own stores.)
7. **No RBAC layer.** Authorization is composed via the
   `ActionFirewall` + `PolicyEngine`; there is no dedicated role
   mapping step in the A2A filter itself.
8. **Single artifact per response.** The adapter wraps
   `HandoffResponse.result` into a single `A2AArtifact`; multiple
   artifacts per response require a richer response contract (planned
   for v2).
9. **No transport-level retries.** Use `A2AClient.send_with_timeout`
   for deadline enforcement; transport-level retry/backoff is the
   caller's responsibility.
10. **`INPUT_REQUIRED` is set but not actively driven.** A HITL-blocked
    task is parked in `SUBMITTED` with `pending_approval_id`; the A2A
    layer does not yet automatically re-dispatch when an approval is
    later granted (caller must re-submit).

---

## 15. Composition over Duplication

Every A2A component is thin: it reuses the existing spine and adds only
the A2A-specific vocabulary. No security, audit, evidence, provenance,
execution or handoff logic was reimplemented.

| A2A component          | Reuses (existing)                                  | Adds (new)                                                       |
|------------------------|----------------------------------------------------|------------------------------------------------------------------|
| `AgentCard`            | `AgentManifest`, `AgentIdentity`                   | A2A wire schema, `manifest_to_card()` derivation                 |
| `A2ATask` / `A2AMessage` / `A2AArtifact` | `core.types` (`new_id`, `utc_now`)    | A2A protocol contract models                                    |
| `TaskState`            | —                                                  | A2A's six-state lifecycle + `_STATUS_MAP` to HandoffStatus       |
| `A2AAdapter`           | `HandoffRequest`, `HandoffResponse`, `RiskLevel`   | `task_to_handoff_request` + `handoff_response_to_task`           |
| `A2ASecurityFilter`    | `Authenticator`, `ActionFirewall`, `PolicyEngine`, `ApprovalEngine`, `RunContext` | `A2A_RECEIVE_ACTION` constant, `A2ASecurityDecision` model |
| `A2AServer`            | `HandoffManager`, `AgentManifest`, audit/evidence/provenance observers | `receive` / `get_status` / `cancel` lifecycle, idempotency dict |
| `A2AClient`            | `A2ATransport`, `core.types`                       | Transport-agnostic send/status/cancel API                        |
| `InProcessTransport`   | `A2ATransport` ABC                                 | Dict-backed endpoint registry                                   |
| `HTTPTransport`        | `A2ATransport` ABC                                 | Lazy `httpx` import guard; methods IMPLEMENTED (real HTTP via `httpx.AsyncClient`) |
| `A2ARegistry`          | Mirrors `AgentRegistry` shape                      | `A2AAgentRecord` for A2A peers                                   |
| `a2a.observers`        | `COMPONENT_AUDIT_LOGGER`, `COMPONENT_EVIDENCE_COLLECTOR`, `provenance_tracker` | Lazy no-op fallbacks, `A2A_RECEIVE_ACTION` action name |

**Total new code added to the seed core:** ~1524 lines across 9 files
in `src/albra_seed/a2a/`. **Zero lines modified in**
`handoff/`, `execution/`, `evidence/`, `provenance/`, `audit/`,
`security/`, `firewall/`, `policy/`, `approvals/`, `mcp/`.

**Two non-breaking additions:**
- `core/identity.py`: added `endpoint: str | None = None` field (A2A
  discovery URL; `None` in CORE_ONLY).
- `albra_seed/__init__.py`: 16 lazy `__getattr__` exports so
  `import albra_seed` does not pull in the A2A subpackage.

A2A proves the seed's composition contract: a new external protocol can
be added as a pure adapter without touching any of the spine components
it rides on.

---

*See also: [`ARCHITECTURE.md`](ARCHITECTURE.md) · [`EXTENDING.md`](EXTENDING.md) · [`SPECIALIZATION.md`](SPECIALIZATION.md) · [`SECURITY.md`](SECURITY.md)*
