# ALBRA AGENT SEED V1.0 — Certification Status

> **Canonical single source of truth for ALBRA SEED certification state.**

**Last verified:** 2026-10-13
**Commit:** `<TBD-final>` — will be set to the actual SHA after this commit
**Status:** `AUDIT_CANDIDATE` — pending independent audit. NOT READY_TO_FREEZE.

---

## Canonical Numbers (verified 2026-10-13)

| Metric | Value | Source |
|---|---|---|
| Universal DNA components | 106 | `src/albra_seed/parity/dna.py` |
| Audit matrix probes | 40 | `scripts/audit_matrix.py` |
| Total tests | 833 | `pytest --collect-only -q` |
| A2A tests | 72 | `tests/a2a/` |
| Golden flows | 13 | `tests/golden/` |
| Security tests | ~80 | `tests/security/` |
| Cognitive cycle tests | 15+16 | `tests/unit/test_cognitive_*.py` |

## Gate Results (local verification, 2026-10-13)

| Gate | Result | Evidence |
|---|---|---|
| Tests | PASS | 833 passed in ~17s |
| Audit Matrix | PASS | 40/40 VERIFIED |
| Genetic Parity | PASS | exit 0, 106/106 DNA, is_pure_seed=True |
| Genetic Contamination | PASS | exit 0, is_clean=True |
| Clean-room | PASS | 17/17 + 2/2 |

## Python version verification

| Python | Tests | Result |
|---|---|---|
| 3.10 | 833 | passed |
| 3.11 | 833 | passed |
| 3.12 | 833 | passed |

## CI GitHub Actions

**Not executed for this branch.** The workflow only triggers on `push: main` or
`pull_request: main`. To execute CI, a PR must be opened (pending authorization).

## What is verified vs what is CI-only

### Verified locally (in-process)
- All 833 tests pass on Python 3.10, 3.11, 3.12 (fresh venvs)
- Audit matrix, parity, contamination, clean-room all PASS
- Security perimeter: fail-closed executor, TrustedPrincipal seal verification,
  dry-run without invocation, budget reservation before dispatch
- Cognitive cycle: iteration/time/call limits, loop prevention
- Memory: retrieval + sanitization, isolation, cleanup, decision recording
- A2A: wire format, HTTP transport, external interop (via httpx.ASGITransport)

### Not verified (requires CI or external infrastructure)
- Fresh git clone + fresh venv + `pip install -e .[dev,a2a,api]` (CI only)
- Real network A2A interop (best-effort via ASGITransport)
- External provider integration (OpenAI/Anthropic/etc.)

## Limitations

1. **Retrieval is lexical, not semantic** — candidate_limit bounds the scan (default 10000). At very large scales (>10k memories in one isolation bucket), a relevant OLD memory could be excluded from the candidate set before ranking. This is a known scalability limitation.
2. **TrustedPrincipal seal key is per-process** — the HMAC-SHA256 key lives in memory. A process memory dump could expose it.
3. **verified_principal is a public field** — the guarantee is that the ToolExecutor rejects any object that is not a TrustedPrincipal with a valid cryptographic seal, NOT that the field itself is private.
4. **CI not executed on this branch** — requires PR.

## Status

`AUDIT_CANDIDATE` — ready for independent audit. NOT approved. NOT frozen.
