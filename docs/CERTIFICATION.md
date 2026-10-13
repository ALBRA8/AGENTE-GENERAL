# ALBRA AGENT SEED V1.0 — Certification Status

> **Canonical single source of truth for ALBRA SEED certification state.**

**Last verified:** 2026-10-14
**Status:** `AUDIT_CANDIDATE` — pending independent audit. NOT `READY_TO_FREEZE`.

---

## SHA registry (auditor's reference)

This section records every SHA relevant to the certification. The
auditor can verify each one directly in GitHub via `git ls-remote`
or the GitHub UI.

| SHA role | Full SHA | Branch | Notes |
|---|---|---|---|
| **Audited code candidate** | `16441eb` (full SHA resolved after publish) | `feat/albra-universal-core` | Commit that contains all code + test fixes (M-01/M-02/M-03 + Bloqueantes 1-3). This is the SHA the auditor should review against `main`. |
| **PR HEAD** | (resolved after publish) | `feat/albra-universal-core` | The tip of the PR #2 branch — includes this doc commit on top of the audited code candidate. |
| **CI run SHA** | matches PR HEAD | `feat/albra-universal-core` | GitHub Actions runs on the PR HEAD; CI verdict applies to the same SHA. |
| `main` (base) | `43563c81c7a77bb9a762921037fd7f91414cb212` | `main` | Untouched baseline. |
| PR #1 candidate | `f0ca9e3d24fd84c0a0a9e5579816545cea983a7e` | `fix/memory-hardening-phase-2` | Sub-PR whose hardening commits were cherry-picked into PR #2 (Bloqueante 1). |

> **Note on the "SHA document refers to itself" problem:** a doc
> commit that records its own SHA does not exist yet at the time the
> doc is written. The convention used here: the **audited code
> candidate** SHA is `16441eb` (a code+test commit, not a doc commit).
> Any doc commit ON TOP of `16441eb` that records `16441eb` as the
> audited candidate is correct — the auditor should verify `16441eb`
> directly in GitHub and review the diff between `43563c8` (main)
> and `16441eb`.

---

## Canonical Numbers

| Metric | Value | Source |
|---|---|---|
| Universal DNA components | 106 | `src/albra_seed/parity/dna.py` |
| Audit matrix probes | 40 | `scripts/audit_matrix.py` |
| Total tests (local, with z-ai CLI) | 934 | `pytest --collect-only -q` |
| Total tests (CI, without z-ai CLI) | 929 passed + 1 skipped | GitHub Actions |
| A2A tests | 72 | `tests/a2a/` |
| Golden flows | 13 | `tests/golden/` |
| Security tests | ~142 | `tests/security/` (includes 11 M-01/M-02, 3 B2, 14 B3) |
| Cognitive cycle tests | 31 | `tests/unit/test_cognitive_*.py` |

## Gate Results

| Gate | Result | Evidence |
|---|---|---|
| Tests (local) | PASS | 934 passed, 1 warning (asyncio ResourceWarning) |
| Tests (CI) | PASS | 929 passed + 1 skipped (z-ai module), 3/3 jobs success |
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

- **Local (with z-ai CLI):** 934 passed + 1 warning (asyncio ResourceWarning, not our code)
- **CI (without z-ai CLI):** 929 passed + 1 skipped (`tests/llm_real/` module-level skip)
- **Formula:** 934 local − 5 `tests/llm_real/` tests = 929 passed + 1 module-level skip

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

## Status

`AUDIT_CANDIDATE` — CI green on the audited candidate. Ready for
independent audit. NOT approved. NOT frozen.

The candidate is NOT `READY_TO_FREEZE` — it is `AUDIT_CANDIDATE`
pending independent audit approval.

## Integration history (2026-10-13 / 2026-10-14)

This branch integrates the following fixes via cherry-pick and direct
commits, resolving all blockers identified by the independent audit:

- **H-18** (doc-only): `docs/CERTIFICATION.md` SHA reconciliation.
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
