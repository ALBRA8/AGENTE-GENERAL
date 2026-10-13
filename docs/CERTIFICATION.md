# ALBRA AGENT SEED V1.0 — Certification Status

> **Canonical single source of truth for ALBRA SEED certification state.**

**Last verified:** 2026-10-13
**Commit:** `f68b67d` (full SHA resolved after publish — see GitHub)
**Status:** `AUDIT_CANDIDATE` — pending independent audit. NOT READY_TO_FREEZE.

---

## Canonical Numbers

| Metric | Value | Source |
|---|---|---|
| Universal DNA components | 106 | `src/albra_seed/parity/dna.py` |
| Audit matrix probes | 40 | `scripts/audit_matrix.py` |
| Total tests (local, with z-ai CLI) | 927 | `pytest --collect-only -q` |
| Total tests (CI, without z-ai CLI) | 922 passed + 1 skipped | GitHub Actions |
| A2A tests | 72 | `tests/a2a/` |
| Golden flows | 13 | `tests/golden/` |
| Security tests | ~135 | `tests/security/` |
| Cognitive cycle tests | 31 | `tests/unit/test_cognitive_*.py` |

## Gate Results

| Gate | Result | Evidence |
|---|---|---|
| Tests (local) | PASS | 927 passed, 1 warning (asyncio ResourceWarning) |
| Tests (CI) | PASS | 922 passed + 1 skipped (z-ai module), 3/3 jobs success |
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

- **Local (with z-ai CLI):** 927 passed + 1 warning (asyncio ResourceWarning, not our code)
- **CI (without z-ai CLI):** 922 passed + 1 skipped (`tests/llm_real/` module-level skip)
- **Formula:** 927 local - 5 llm_real tests = 922 passed + 1 module-level skip

## Limitations

1. **Retrieval is lexical, not semantic** — candidate_limit bounds the scan (default 10000).
2. **TrustedPrincipal seal key is per-process** — HMAC-SHA256 key lives in memory.
3. **1 skipped in CI:** `tests/llm_real/` — z-ai CLI not available on GitHub runners.
4. **1 warning:** asyncio ResourceWarning (not from our code).

## Status

`AUDIT_CANDIDATE` — CI green on the integrated SHA. Ready for independent audit. NOT approved. NOT frozen.

## Integration notes (2026-10-13)

This commit integrates the M-01/M-02/M-03 fixes (originally developed on
`fix/memory-hardening-phase-2`) into the candidate branch via cherry-pick,
resolving the following blockers identified by the independent audit:

- **Bloqueante 1:** Memory hardening (M-01 trust-level separation, M-02
  response redaction, M-03 restored assertions) now present in the
  candidate branch. Memory is delivered as `Role.TOOL` (structurally
  untrusted), not `Role.SYSTEM`.
- **Bloqueante 2:** Redactor is fail-closed. If `redact_secrets` raises,
  the response content is replaced with a safe placeholder — original
  content is NEVER delivered on redactor failure. 3 new tests cover
  this policy.
- **Bloqueante 3:** Every supported provider (OpenAI, Anthropic, Groq,
  Ollama, ZAI) explicitly serializes `Role.TOOL` to a user-role
  message with an UNTRUSTED DATA prefix. No provider emits
  `role: tool` without a `tool_call_id` (which would cause HTTP 400).
  7 new tests cover provider serialization.
- **Bloqueante 4:** This document was stale (pointed to `2c78ebe`,
  counts 906/901, status implied frozen). Now points to the integrated
  SHA `f68b67d`, counts 927/922, status `AUDIT_CANDIDATE`.
- **Bloqueante 5:** CI green on Python 3.10, 3.11, 3.12. See the
  Actions tab for the exact run URL.

The candidate is NOT `READY_TO_FREEZE` — it is `AUDIT_CANDIDATE`
pending independent audit approval.
