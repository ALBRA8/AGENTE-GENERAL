# ALBRA AGENT SEED V1.0 — Memory (MemoryDV)

> La memoria de la semilla se llama **MemoryDV** — Data + Verifiable. Cada
> recuerdo lleva proveniencia, aislamiento estricto, y disciplina de evidencia.
> El Seed NO recuerda nada por defecto; cada write es una decisión explícita.

This document covers the memory subsystem: types, isolation, provenance,
evidence, consolidation, backends, lifecycle.

---

## 1. The Five Memory Types

```python
class MemoryType(str, Enum):
    EPISODIC    = "episodic"     # event-bound: "what happened at time T"
    SEMANTIC    = "semantic"     # generalized knowledge derived from episodes
    FACTUAL     = "factual"      # verified facts (requires evidence + confidence)
    PROCEDURAL  = "procedural"   # how-to: skills / recipes / playbooks
    WORKING     = "working"      # short-term scratch for the current execution
```

| Type         | Persists?     | Requires evidence? | Typical content                                       |
| ------------ | ------------- | ------------------ | ----------------------------------------------------- |
| `EPISODIC`   | yes           | no                 | "Agent ran tool X with input Y at 12:03 UTC."          |
| `SEMANTIC`   | yes           | no (derived)       | Generalized from episodic via consolidation.          |
| `FACTUAL`    | yes           | **YES (≥1 evidence_id)** | Verified knowledge from tool / external / verified_provider source. |
| `PROCEDURAL` | yes           | no                 | "To invoke X, run steps A, B, C."                     |
| `WORKING`    | per-execution | no                 | Scratchpad for the current execution_id; cleared when execution ends. |

---

## 2. Memory Isolation

**Location**: `albra_seed/memory/isolator.py`

Every memory record lives in a bucket keyed by a 4-tuple:

```
(agent_id, domain, user_id, context_id)
```

`MemoryIsolator.key_for(...)` produces an immutable `MemoryKey`. Two records
are in the same bucket iff ALL four components match.

### 2.1 Why four components?

| Component     | Purpose                                                           | Example                              |
| ------------- | ---------------------------------------------------------------- | ------------------------------------ |
| `agent_id`    | Distinguishes memory of different specialized agents.            | `albra-legal-agent` vs `albra-sales-agent` |
| `domain`      | Distinguishes memory namespaces within one agent.                | `"legal"` vs `"case_law"`            |
| `user_id`     | Distinguishes memory belonging to different end-users.           | `"user-123"` vs `"user-456"`         |
| `context_id`  | Distinguishes memory belonging to different conversations / cases. | `"case-2024-001"` vs `"case-2024-002"` |

### 2.2 Enforcement

`MemoryManager.remember()` and `MemoryManager.recall()` ALWAYS route through
`MemoryIsolator`. A legal agent CANNOT read a sales agent's memory. A legal
agent working on case A CANNOT read the same agent's memory about case B
(unless explicitly queried with `context_id=None`).

```python
mgr.remember(agent_id="agent-A", domain="legal", user_id="u1",
             context_id="case-1", content=...)
# This record is invisible to:
#   recall(agent_id="agent-B", ...)        # different agent
#   recall(agent_id="agent-A", domain="sales", ...)  # different domain
#   recall(agent_id="agent-A", domain="legal", user_id="u2", ...)  # different user
#   recall(agent_id="agent-A", domain="legal", user_id="u1", context_id="case-2")  # different context
```

`MemoryIsolator.assert_isolated()` raises `PermissionError` on a violation.

---

## 3. Provenance on Every Memory Write

**Location**: `albra_seed/memory/provenance.py`

Every `MemoryRecord` carries a `MemoryProvenance` block attached at write time:

| Field                | Meaning                                              |
| -------------------- | ---------------------------------------------------- |
| `provenance_id`      | Unique ID for this provenance record.                |
| `source`             | Name of the system that produced the memory.         |
| `source_type`        | `"agent" | "tool" | "provider" | "user" | "external" | "verified_provider"` |
| `timestamp`          | When the memory was created.                         |
| `execution_id`       | Execution that produced it (for replay).             |
| `correlation_id`     | Cross-component trace ID.                            |
| `confidence`         | 0.0 – 1.0.                                           |
| `evidence_ids`       | Linked evidence records (required for FACT).          |
| `parent_provenance_id` | If derived from another record, that record's ID.  |

### 3.1 Attachment is automatic

`MemoryManager.remember()` always calls `provenance.attach(record)` before
writing. There is no "write memory without provenance" code path.

```python
record = await mgr.remember(
    agent_id="agent-A", domain="legal",
    content={"rule": "statute of limitations is 5 years"},
    source="law_database",
    source_type="external",
    confidence=0.95,
    evidence_ids=["ev_001", "ev_002"],
    is_fact=True,
)
assert record.provenance_id is not None
assert record.evidence_ids == ["ev_001", "ev_002"]
```

---

## 4. Evidence Required for FACT Type

**Location**: `albra_seed/memory/manager.py`, `albra_seed/evidence/contract.py`

The `MemoryManager.remember()` method enforces:

```
is_fact=True  ──→  evidence_ids non-empty (MemoryProvenance gate)
                  AND
                  evidence must satisfy EvidenceContract.validate():
                      evidence_type == FACT
                      confidence >= 0.9
                      source_type ∈ {tool, external, verified_provider}
```

If `is_fact=True` is passed without evidence_ids, the manager raises
`ValueError`:

```python
try:
    await mgr.remember(
        agent_id="A", domain="legal",
        content={"rule": "..."},
        is_fact=True,                # ← no evidence_ids!
    )
except ValueError as exc:
    print(exc)   # is_fact=True requires at least one evidence_id
```

### 4.1 UNKNOWN never becomes FACT

The `EvidenceContract.promote_to_fact()` method has a hard rule:

```python
if evidence.evidence_type == EvidenceType.UNKNOWN:
    raise EvidenceValidationError(
        "UNKNOWN evidence cannot be promoted to FACT (contract hard rule)"
    )
```

This is enforced in code AND covered by `tests/security/test_security.py`.

---

## 5. Consolidation Policy

**Location**: `albra_seed/memory/consolidation.py`

`ConsolidationPolicy` operates on the EPISODIC → SEMANTIC axis only.

### 5.1 Promotion path

```
EPISODIC record
    │
    │  confidence >= threshold (default 0.7)
    │  AND
    │  access_count >= min_access_count (default 0)
    │  AND
    │  (if require_evidence=True) at least one evidence_id
    ▼
SEMANTIC record (version += 1)
```

### 5.2 Hard rule: NEVER promotes UNKNOWN → FACT

`ConsolidationPolicy.can_promote_to_fact()` is hard-coded to return `False`:

```python
def can_promote_to_fact(self, record: MemoryRecord) -> bool:
    """Hard gate: this policy never promotes anything to FACT.
    FACT promotion must go through the EvidenceContract instead.
    """
    return False
```

FACT promotion is the EXCLUSIVE responsibility of `EvidenceContract.promote_to_fact()`,
which itself refuses UNKNOWN evidence (see §4.1).

### 5.3 Manual invocation

```python
result = await mgr.consolidate(record_id, by="consolidator")
if result.promoted:
    print(f"{result.old_type} → {result.new_type}: {result.reason}")
```

---

## 6. Memory Backends

**Location**: `albra_seed/memory/{inmemory_store,sqlite_store,vector_store}.py`

| Backend                 | Storage                            | Dependencies                  | Use case                              |
| ----------------------- | ---------------------------------- | ----------------------------- | ------------------------------------- |
| `InMemoryMemoryStore`   | Python dict                        | none                          | Tests, CORE_ONLY mode, ephemeral runs. |
| `SQLiteMemoryStore`     | SQLite file (or `:memory:`)        | stdlib `sqlite3`              | Local persistence, no extra deps.     |
| `VectorMemoryStore`     | Chroma collection                   | `chromadb` (lazy)             | Semantic similarity search (optional). |

### 6.1 Backend interface (MemoryStore ABC)

```python
class MemoryStore(ABC):
    async def write(self, record: MemoryRecord) -> str: ...
    async def read(self, record_id: str) -> MemoryRecord | None: ...
    async def query(self, *, agent_id, domain="default",
                    user_id=None, context_id=None,
                    memory_type=None, limit=50) -> list[MemoryRecord]: ...
    async def delete(self, record_id: str) -> bool: ...
    async def count(self, *, agent_id, domain="default") -> int: ...
```

### 6.2 Backend selection

```python
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.memory.sqlite_store import SQLiteMemoryStore
# from albra_seed.memory.vector_store import VectorMemoryStore  # requires chromadb

store = InMemoryMemoryStore()                     # CORE_ONLY default
# or
store = SQLiteMemoryStore(path="./data/memory.sqlite3")
# or
# store = VectorMemoryStore(collection="albra-default")

mgr = MemoryManager(store=store)
```

### 6.3 Tombstones

Every backend records a tombstone on `delete()`:

| Backend             | Tombstone form                                |
| ------------------- | --------------------------------------------- |
| `InMemoryMemoryStore` | `_tombstones[record_id] = {deleted_at, by, reason}` |
| `SQLiteMemoryStore`  | Deleted rows moved to `memory_tombstones` table. |

The tombstone satisfies audit requirements: "this record existed and was
deleted at time T by actor A for reason R."

---

## 7. Memory Lifecycle

**Location**: `albra_seed/memory/lifecycle.py`

Each record has a lifecycle tracked by `MemoryLifecycle` events:

```
CREATED ──▶ MODIFIED ──▶ ACCESSED ──▶ CONSOLIDATED ──▶ ARCHIVED ──▶ DELETED ──▶ FORGOTTEN
   ▲           │            │             │
   │           └──── (may revisit MODIFIED / ACCESSED any number of times) ──┘
```

### 7.1 Lifecycle states

| State          | Meaning                                                          |
| -------------- | ---------------------------------------------------------------- |
| `CREATED`      | Record was written for the first time.                            |
| `MODIFIED`     | Record content was updated (version incremented).                 |
| `ACCESSED`     | Record was read (e.g., by `recall`).                              |
| `CONSOLIDATED` | Record was promoted (e.g., EPISODIC → SEMANTIC).                  |
| `ARCHIVED`     | Record marked read-only (e.g., case closed).                      |
| `DELETED`      | Record deleted; tombstone retained.                               |
| `FORGOTTEN`    | Record explicitly forgotten (e.g., GDPR right-to-be-forgotten).   |

### 7.2 Event recording

`MemoryManager` records events automatically:

```python
record = await mgr.remember(...)        # CREATED event
results = await mgr.recall(...)         # ACCESSED event per result
result = await mgr.consolidate(record_id)  # CONSOLIDATED event (if promoted)
ok = await mgr.forget(record_id, reason="user requested")  # DELETED + FORGOTTEN events
```

### 7.3 History query

```python
events = mgr.lifecycle.history(record_id)
for ev in events:
    print(ev.state, ev.occurred_at, ev.by, ev.reason)

current = mgr.lifecycle.current_state(record_id)
alive   = mgr.lifecycle.is_alive(record_id)
```

### 7.4 Audit integration

For durable lifecycle audit (production), snapshot
`mgr.lifecycle.history(record_id)` into the audit log via the agent loop's
`RECORD` phase or via `audit_logger.record_async(...)`.

---

## 8. MemoryManager API Summary

```python
class MemoryManager:
    async def remember(
        self, *,
        agent_id: str,
        content: Any,
        memory_type: MemoryType = MemoryType.EPISODIC,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        confidence: float = 0.0,
        source: str = "agent",
        source_type: str = "agent",
        evidence_ids: list[str] | None = None,
        execution_id: str | None = None,
        correlation_id: str | None = None,
        is_fact: bool = False,
    ) -> MemoryRecord: ...

    async def recall(
        self, *,
        agent_id: str,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        memory_type: MemoryType | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]: ...

    async def forget(
        self, record_id: str, *,
        reason: str | None = None,
        by: str | None = None,
    ) -> bool: ...

    async def consolidate(
        self, record_id: str, *,
        by: str = "consolidator",
    ) -> ConsolidationResult: ...

    async def get(self, record_id: str) -> MemoryRecord | None: ...
    async def count(self, *, agent_id: str, domain: str = "default") -> int: ...
```

---

## 9. Memory + Agent Loop Integration

The agent loop's `RECORD` phase (`albra_seed/runtime/agent.py`) automatically:

1. Calls `MemoryStore.write(...)` with a `WORKING` memory record containing
   the inbound message content + execution_id + correlation_id.
2. The `evidence_collector` component (if registered) records any
   tool-produced evidence into the `EvidenceStore`.
3. The `audit_logger` component (if registered) appends a normal audit
   entry for the execution.
4. The tamper-evident audit log (if a `KillSwitch` audit sink is wired)
   records the kill-switch / approval events.

The seed ships NO domain memory writes — every specialization layer is
responsible for its own domain memories (with provenance + evidence).

---

## 10. Invariant Checklist

These invariants are enforced by code AND covered by tests:

- [x] Memory isolated by composite key `(agent_id, domain, user_id, context_id)`.
- [x] Provenance attached automatically on every `remember()`.
- [x] `is_fact=True` requires `evidence_ids` non-empty.
- [x] FACT evidence requires `confidence >= 0.9` AND qualifying `source_type`.
- [x] UNKNOWN evidence NEVER auto-promotes to FACT.
- [x] Consolidation operates EPISODIC → SEMANTIC only; never promotes to FACT.
- [x] Tombstones retained on delete (audit traceability).
- [x] Lifecycle events recorded automatically (CREATED / ACCESSED / etc.).
- [x] Three backends (InMemory / SQLite / Vector) all implement the same
      `MemoryStore` ABC.
- [x] Memory empty at seed init — the seed knows nothing.

---

## 11. Specialization Boundary

A specialization layer MUST NOT:

- Bypass `MemoryIsolator` (all writes/recalls go through it).
- Write `is_fact=True` without evidence_ids (the manager refuses).
- Promote UNKNOWN evidence to FACT (the contract refuses).
- Replace the MemoryStore ABC (use one of the three backends or implement
  the ABC in a new backend).
- Cross agent_id / domain / user_id / context_id boundaries (the isolator
  raises `PermissionError`).

A specialization layer MAY:

- Register additional MemoryStore backends (e.g., Redis, Postgres).
- Define its own memory domains (e.g., `"legal"`, `"sales"`).
- Override the default consolidation threshold.
- Subscribe to lifecycle events for domain-specific side-effects (e.g.,
  notify an external CRM when a record is FORGOTTEN).

See [`SPECIALIZATION.md`](SPECIALIZATION.md) for the full contract.
