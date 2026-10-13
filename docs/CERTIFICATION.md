# ALBRA AGENT SEED V1.0 — Certification Status

> **Canonical single source of truth for ALBRA SEED certification state.**

**Last verified:** 2026-10-13 (UTC)
**Status:** `AUDIT_CANDIDATE` — pending independent audit. NOT `READY_TO_FREEZE`.

---

## SHA registry (auditor's reference)

Every SHA below is verifiable in GitHub via `git ls-remote origin` or
the GitHub UI. The auditor should treat the **Audited code candidate**
as the SHA to review against `main`.

| SHA role | Full SHA | Branch | Notes |
|---|---|---|---|
| **Audited code candidate** | `2e2c64f68c49790e01d4755b6c4986a8820029e4` | `feat/albra-universal-core` | Commit that contains all code + test fixes through Tareas 1-2 (M-01/M-02/M-03 + Bloqueantes 1-3 + B-SEC-AUTONOMY + REQUIRE_HITL semantics + execution boundary). This is the SHA the auditor reviews against `main`. |
| **PR HEAD** | (resolved after publish — see GitHub) | `feat/albra-universal-core` | The tip of PR #2 — includes any doc-only commit on top of the audited candidate. |
| **CI run SHA** | matches PR HEAD | `feat/albra-universal-core` | GitHub Actions runs on the PR HEAD; CI verdict applies to that SHA. |
| `main` (base) | `43563c81c7a77bb9a762921037fd7f91414cb212` | `main` | Untouched baseline. |
| PR #1 candidate | `f0ca9e3d24fd84c0a0a9e5579816545cea983a7e` | `fix/memory-hardening-phase-2` | Sub-PR whose hardening commits were cherry-picked into PR #2 (Bloqueante 1). |

> **Note on the "SHA document refers to itself" problem:** a doc commit
> that records its own SHA does not exist yet at the time the doc is
> written. The convention used here: the **audited code candidate** is
> `2e2c64f68c49790e01d4755b6c4986a8820029e4` (a code+test commit, not
> a doc commit). Any doc commit ON TOP of `2e2c64f` that records
> `2e2c64f` as the audited candidate is correct. The auditor should
> verify `2e2c64f` directly in GitHub and review the diff between
> `43563c8` (main) and `2e2c64f` (audited candidate).

---

## Canonical Numbers

| Metric | Value | Source |
|---|---|---|
| Universal DNA components | 106 | `src/albra_seed/parity/dna.py` |
| Audit matrix probes | 40 | `scripts/audit_matrix.py` |
| Total tests (local, with z-ai CLI) | 962 | `pytest --collect-only -q` |
| Total tests (CI, without z-ai CLI) | 957 passed + 1 skipped | GitHub Actions |
| A2A tests | 72 | `tests/a2a/` |
| Golden flows | 13 | `tests/golden/` |
| Security tests | ~157 | `tests/security/` (incl. 11 M-01/M-02, 3 B2, 14 B3, 13 B-SEC-AUTONOMY, 7 REQUIRE_HITL semantics, 8 REQUIRE_HITL execution boundary) |
| Cognitive cycle tests | 31 | `tests/unit/test_cognitive_*.py` |

## Gate Results

| Gate | Result | Evidence |
|---|---|---|
| Tests (local) | PASS | 962 passed, 1 warning (asyncio ResourceWarning) |
| Tests (CI) | PASS | 957 passed + 1 skipped (z-ai module), 3/3 jobs success |
| Audit Matrix | PASS | 40/40 VERIFIED |
| Genetic Parity | PASS | exit 0, 106/106 DNA, is_pure_seed=True |
| Genetic Contamination | PASS | exit 0, is_clean=True |
| Clean-room | PASS | 17/17 + 2/2 |

## CI GitHub Actions

| Job | Status | Conclusion |
|---|---|---|
| Python 3.10 on ubuntu-latest | completed | success |
| Python 3.11 on ubuntu-latest | completed | success |
| Python 3.12 on ubuntu-latest | completed | success |

CI runs on every push to `feat/albra-universal-core` via PR #2 trigger.

## Test count reconciliation

- **Local (with z-ai CLI):** 962 passed + 1 warning (asyncio ResourceWarning, not our code)
- **CI (without z-ai CLI):** 957 passed + 1 skipped (`tests/llm_real/` module-level skip)
- **Formula:** 962 local − 5 `tests/llm_real/` tests = 957 passed + 1 module-level skip

## Limitations and known gaps

1. **Retrieval is lexical, not semantic** — `MemoryRetriever` uses
   keyword overlap scoring; `candidate_limit` bounds the scan (default
   10000). Semantic embedding-based retrieval is out of scope for V1.0.

2. **TrustedPrincipal seal key is per-process** — the HMAC-SHA256 key
   used to seal `TrustedPrincipal` instances lives in memory. A
   process restart invalidates all outstanding seals. This is by
   design (no shared secret across processes); persistence would
   require a key-management subsystem that is out of scope.

3. **1 skipped in CI:** `tests/llm_real/` — 5 tests that use a REAL
   LLM (GLM-4-plus via z-ai CLI) to validate the memory injection
   perimeter against an actual model. They skip on GitHub runners
   (no z-ai CLI available) and run only locally. They are OBSERVATIONAL
   — they document the LLM's text behavior but do NOT constitute a
   security proof (the structural perimeter is the security proof).

4. **1 warning:** asyncio ResourceWarning (not from our code).

5. **Sanitizer léxico evitable** — `redact_secrets` and
   `sanitize_injections` use regex patterns. Paraphrasing,
   obfuscation, and unicode tricks can evade them. The structural
   perimeter (firewall/policy/executor does NOT read memory content)
   is the primary security barrier; the sanitizer is defense-in-depth.

6. **M-02 redactor only catches HIGH-SIGNAL secret patterns** —
   `sk-...`, `Bearer ...`, hex ≥32, base64 ≥32, inline `key=value`
   patterns with values ≥16 chars. Short secrets (OTP 6 digits,
   PIN 4 digits) are NOT redacted by the response redactor (the
   memory sanitizer DOES catch them via `_SHORT_CODE_KEYS`).

7. **Provider API compatibility is verified via SIMULATED clients** —
   the B3 provider tests stub the HTTP client and verify the SERIALIZED
   payload (the mapping from `Message` to API-specific dict). They do
   NOT test against the real OpenAI/Anthropic/Groq/Ollama/ZAI APIs.
   Real-API testing would require credentials and live network calls,
   which are out of scope for the CI pipeline. The simulated tests
   verify the security-relevant code path (the role mapping logic).

8. **Identity-based access control on retrieval is unchanged** —
   `MemoryRetriever.retrieve()` already scopes by
   `(agent_id, user_id, domain, context_id)`. M-02 did NOT add
   per-field ACLs on memory records; it added a last-mile response
   redactor. If the auditor requires per-field ACLs, that is a
   separate finding.

9. **REQUIRE_HITL can be upgraded by HITL — by design.** A human
   approval CAN convert a REQUIRE_HITL verdict (autonomy_insufficient)
   to ALLOW. This is the standard HITL semantics. The engine returns
   DENY (not REQUIRE_HITL) for absolute restrictions: a DENY rule, a
   missing permission, or an unverifiable autonomy level (manager
   raised). The current `PolicyRule` model has no
   `absolute_restriction` field — DENY rules are the mechanism for
   "never executable regardless of HITL". See
   `tests/security/test_require_hitl_semantics.py` for the full
   semantics documentation.

## Status

`AUDIT_CANDIDATE` — CI green on the audited candidate. Ready for
independent audit. NOT approved. NOT frozen.

The candidate is NOT `READY_TO_FREEZE` — it is `AUDIT_CANDIDATE`
pending independent audit approval.

## Integration history

This branch integrates the following fixes via cherry-pick and direct
commits, resolving all blockers identified by the independent audit:

- **H-18** (doc-only): SHA reconciliation in this document.
- **Bloqueante 1** (M-01/M-02/M-03): Memory hardening — `Role.TOOL`
  for untrusted retrieved content, response redaction, restored
  assertions. Cherry-picked from `fix/memory-hardening-phase-2`.
- **Bloqueante 2**: Fail-closed redactor policy — if `redact_secrets`
  raises, response content is replaced with a safe placeholder.
  3 new tests.
- **Bloqueante 3**: `Role.TOOL` serialization per provider. All 5
  supported providers (OpenAI, Anthropic, Groq, Ollama, ZAI) map
  `Role.TOOL` → user-role message with UNTRUSTED DATA prefix. 14
  tests (6 source-inspection + 8 behavioral with stubbed clients).
- **Bloqueante 4**: This document reconciled with audited SHA, counts,
  and limitations.
- **Bloqueante 5**: CI green on Python 3.10, 3.11, 3.12.
- **B-SEC-AUTONOMY** (fail-closed authorization): `PolicyEngine.evaluate()`
  now defaults `autonomy_ok = False` (fail-closed). Missing
  `autonomy_manager` → inline comparison (check is NEVER skipped).
  Manager exception → DENY (not REQUIRE_HITL — cannot escalate
  unverifiable gate). 13 new tests (5 reproducing the bug + 8 edge
  cases).
- **Tarea 1** (REQUIRE_HITL semantics): 7 tests covering valid
  approval, rejection, expiration, engine error, action binding,
  absolute restriction (DENY rule), and permission-priority over HITL.
- **Tarea 2** (execution boundary): 8 integration tests with a spy
  tool that counts actual `execute()` invocations. Verifies DENY and
  REQUIRE_HITL verdicts do NOT execute the tool without a valid
  AuthorizationToken. Covers token binding, expiry, revocation, and
  forged principal.
