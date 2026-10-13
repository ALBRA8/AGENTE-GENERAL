# ALBRA AGENT SEED V1.0 — Certification Status

> **Canonical single source of truth for ALBRA SEED certification state.**

**Last verified:** 2026-10-13
**Commit: `b8d9bd16b7bd3af4b2338ef8b68d8a4bb1a3bd74`
**Status:** `AUDIT_CANDIDATE` — pending independent audit. NOT READY_TO_FREEZE.

---

## Canonical Numbers

| Metric | Value | Source |
|---|---|---|
| Universal DNA components | 106 | `src/albra_seed/parity/dna.py` |
| Audit matrix probes | 40 | `scripts/audit_matrix.py` |
| Total tests (local, with z-ai CLI) | 906 | `pytest --collect-only -q` |
| Total tests (CI, without z-ai CLI) | 900 passed + 1 skipped | GitHub Actions |
| A2A tests | 72 | `tests/a2a/` |
| Golden flows | 13 | `tests/golden/` |
| Security tests | ~135 | `tests/security/` |
| Cognitive cycle tests | 31 | `tests/unit/test_cognitive_*.py` |

## Gate Results

| Gate | Result | Evidence |
|---|---|---|
| Tests (local) | PASS | 906 passed, 1 warning (asyncio ResourceWarning) |
| Tests (CI) | PASS | 900 passed + 1 skipped (z-ai module), 3/3 jobs success |
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

- **Local (with z-ai CLI):** 906 passed + 1 warning (asyncio ResourceWarning, not our code)
- **CI (without z-ai CLI):** 900 passed + 1 skipped (`tests/llm_real/` module-level skip)
- **Formula:** 906 local - 5 llm_real tests + 1 module-level skip = 900 + 1 skip

## Limitations

1. **Retrieval is lexical, not semantic** — candidate_limit bounds the scan (default 10000).
2. **TrustedPrincipal seal key is per-process** — HMAC-SHA256 key lives in memory.
3. **1 skipped in CI:** `tests/llm_real/` — z-ai CLI not available on GitHub runners.
4. **1 warning:** asyncio ResourceWarning (not from our code).

## Status

`AUDIT_CANDIDATE` — CI green. Ready for independent audit. NOT approved. NOT frozen.
