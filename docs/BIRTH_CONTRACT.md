# BIRTH CONTRACT — ALBRA AGENT SEED V1.0

> **Status**: Active since GAP-5.
> **Scope**: `src/albra_seed/runtime/runtime.py` (`Runtime.startup()`, `Runtime.birth()`),
> `src/albra_seed/runtime/agent.py` (`Agent._phase_reason`).
> **Invariants**: external providers are NEVER required for birth; Mock is the
> birth default; caller's explicit registration ALWAYS wins.

---

## 1. What "birth" means

An ALBRA seed is **born** when, starting from a bare `Runtime()` constructed
with a default `SeedConfig()` (CORE_ONLY mode — no external provider
credentials), the runtime can:

1. transition `UNINITIALIZED → INITIALIZING → READY`,
2. expose a `provider_router` component,
3. make the always-on Mock provider reachable through that router,
4. execute the universal agent loop end-to-end (RECEIVE → … → RESPOND)
   against the Mock provider, producing a non-empty `AgentResponse`,
5. write and read back a memory record, and
6. append an entry to the audit log.

No external service is contacted. No manual registration is required. The
seed is alive the moment `runtime.startup()` (or, equivalently,
`runtime.birth()`) returns.

## 2. What `Runtime.birth()` verifies

`Runtime.birth()` runs `startup()` if the runtime is still `UNINITIALIZED`
and then returns a snapshot dict with these keys:

| Key                    | Type             | Meaning                                                       |
|------------------------|------------------|---------------------------------------------------------------|
| `identity`             | `AgentIdentity`  | The default seed identity used at birth.                      |
| `seed_version`         | `str`            | From `identity.seed_version` — proves the seed knows itself.  |
| `runtime_ready`        | `bool`           | Result of `runtime.readiness()`.                              |
| `phase`                | `str`            | Lifecycle phase value (expected `"ready"`).                  |
| `mock_available`       | `bool`           | Mock reachable via the router (or runtime's provider map).    |
| `agent_loop_ok`        | `bool`           | `Agent.run("birth")` produced a non-empty response.          |
| `memory_ok`            | `bool`           | A `MemoryRecord` was written and read back identically.       |
| `audit_ok`             | `bool`           | An audit entry was appended via `record_async` / `append`.    |
| `external_providers`   | `list[str]`      | Names of `is_external=True` providers — `[]` in CORE_ONLY.   |
| `bootstrap_source`     | `str | None`     | `"auto"` if startup bootstrapped the router, `"caller"` if    |
|                        |                  | explicitly registered, `None` before startup runs.           |
| `born_at`              | `str` (ISO)      | Timestamp of the snapshot.                                    |

A healthy birth snapshot in CORE_ONLY mode has all four `*_ok` booleans
`True`, `external_providers == []`, `runtime_ready == True`, and
`bootstrap_source == "auto"`.

## 3. External providers are NEVER required for birth

`SeedConfig.is_core_only()` returns `True` whenever none of
`openai_api_key`, `anthropic_api_key`, `groq_api_key`, `zai_api_key`,
`ollama_base_url` are set. In that mode:

* `Runtime.startup()` auto-creates a `ProviderRouter()` (which auto-creates
  a `ProviderRegistry` pre-loaded with the always-on `MockProvider`).
* `Runtime.birth()` does not contact any external service; it executes
  a no-op `Agent.run("birth")` task whose response is produced by the
  Mock provider (echo: `"[mock] birth"`).

External providers (OpenAI / Anthropic / Groq / Z.AI / Ollama) MAY be
registered AFTER birth via `runtime.register_provider(...)` or via the
router's registry, but their absence never blocks birth.

## 4. Mock is the birth default

The `MockProvider` (`src/albra_seed/providers/mock.py`) is the only
provider guaranteed to be present at birth:

* `is_mock = True`, `is_external = False`.
* `health()` always returns `True`.
* `complete(messages, …)` produces a deterministic, content-preserving
  `AgentResponse` (echo of the most-recent USER message, prefixed with
  `"[mock] "`). No randomness, no network call.
* The `ProviderRegistry` constructor auto-registers a `MockProvider`
  instance (`_ensure_mock()`), and `ProviderRegistry.unregister("mock")`
  is a no-op (Mock cannot be removed) — so CORE_ONLY mode always has a
  fallback.

Because `ProviderRouter.__init__` auto-creates a `ProviderRegistry`
(unless one is passed in), instantiating `ProviderRouter()` is sufficient
to obtain a router that can serve Mock-backed completions.

## 5. Caller's explicit registration always wins

`Runtime.startup()` checks `if "provider_router" not in self._components`
before bootstrapping. If the caller has already registered a
`provider_router` (their own `ProviderRouter`, a stub, or any object that
satisfies the routing contract), the bootstrap is SKIPPED and the
caller's component is left untouched.

`Runtime.birth()` applies the same rule to `memory_store` and `audit_logger`:
if the caller has already provided one (via `register_memory_store("default", …)`
or `runtime.register("audit_logger", …)`), birth uses it; otherwise birth
auto-creates an `InMemoryMemoryStore` / `AuditLog` so the verification
can run. The caller can replace these later — `runtime.register(name, …)`
always overwrites the slot.

## 6. Agent provider resolution (post-GAP-5)

`Agent._phase_reason` resolves the LLM provider as follows:

1. If `runtime.register_provider(provider)` has been called at least
   once, the FIRST registered provider (preferring `identity.providers`)
   is called directly. This preserves the pre-GAP-5 contract: callers
   who register a custom Mock / Stub provider expect it to be the one
   that answers every request.
2. Otherwise, the `provider_router` component is called. The router
   iterates its own `ProviderRegistry` and terminates at Mock when no
   other provider is configured.
3. If neither is available (no providers, no router), the phase emits
   a no-op draft so the rest of the loop can proceed (CORE_ONLY
   graceful degradation).

This ordering keeps CORE_ONLY birth working (the router, auto-registered
at startup with Mock, handles the no-op task) AND keeps existing tests
and specialization layers that register a custom provider directly via
`register_provider()` working unchanged.

## 7. Readiness contract

`Runtime.readiness()` returns `True` iff:

* the lifecycle phase is `READY` or `RUNNING`, AND
* every name in `REQUIRED_FOR_READINESS` (`("provider_router",)`) is
  present in `self._components`.

Because `startup()` now auto-registers `provider_router`, `readiness()`
returns `True` immediately after `await runtime.startup()` in CORE_ONLY
mode — no manual `runtime.register("provider_router", …)` required.

`REQUIRED_FOR_READINESS` is intentionally a tuple of exactly one name.
Adding more names would force every specialization layer to register
those components before readiness can be reported — that is the
opposite of the birth contract. Specializations that want stricter
readiness checks can subclass `Runtime` and override `readiness()`.
