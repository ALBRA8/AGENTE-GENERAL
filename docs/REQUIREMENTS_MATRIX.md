# ALBRA AGENT SEED V1.0 — Requirements Matrix

> **Document ID:** `REQUIREMENTS_MATRIX.md`
> **Authority:** Traceability matrix for the post-V1.0 hardening
> (H-01 through H-11). Each row maps a requirement → its code
> location → its proving test → the verified result.
> **Status:** complete as of commit `246a922` + H-09/H-10/H-11
> docs/tests amendment.
> **Scope:** the post-V1.0 hardening is a series of defense-in-depth
> fixes layered on top of the original Universal DNA. None of these
> fixes weaken any existing guarantee; every test that passed at V1.0
> (732 tests) still passes (769 tests total at this write).

---

## How to read this matrix

Each row in the table below is an **independently enforceable**
requirement. The columns:

| Column | Meaning |
|---|---|
| **#**         | The requirement ID (H-01 through H-11). |
| **Requirement** | A one-line statement of what the row enforces. |
| **Code**      | The file:line(s) where the requirement is implemented. |
| **Test**      | The test file::class::method that proves the requirement. |
| **Result**    | The verified result (`PASS` / `N/A`). |

A row's `Result` is `PASS` only when the cited test actually runs and
asserts BOTH the decision AND the effect (for security rows). A row
that merely has a code location but no test is `N/A` (not yet
verified) — there are no such rows at HEAD.

---

## Requirements Matrix

| # | Requirement | Code | Test | Result |
|---|---|---|---|---|
| **H-01** | No direct `tool.execute()` from the agent loop — every dispatch routes through `ToolExecutor`. | `src/albra_seed/runtime/agent.py:1499` (`_phase_execute` calls `executor.execute(...)`); `src/albra_seed/runtime/agent.py:1951` (`_get_tool_executor` lazily constructs + caches the executor). | `tests/security/test_adversarial.py::TestDirectExecutorBypass::test_tool_executor_without_authorization_refuses`; `tests/security/test_adversarial.py::TestDirectExecutorBypass::test_tool_executor_with_allowed_authorization_executes`; `tests/unit/test_cognitive_cycle.py` (smoke). | PASS |
| **H-02** | `ToolExecutor.execute()` requires an explicit `authorization` verdict whose `.allowed is True`; absence or `.allowed is False` → refused (failed `ToolResult` with `output.not_authorized=True`). | `src/albra_seed/tools/executor.py:187` (authorization gate). | `tests/security/test_adversarial.py::TestDirectExecutorBypass::test_tool_executor_without_authorization_refuses`; `tests/security/test_adversarial.py::TestDirectExecutorBypass::test_tool_executor_with_denied_authorization_refuses`; `tests/security/test_adversarial.py::TestDirectExecutorBypass::test_dry_run_bypasses_authorization`. | PASS |
| **H-03** | Real credential flow — `Agent.run(token=...)` threads the token through `ctx["token"]` to `IdentityResolver.resolve_and_attach(run_ctx, token=token)`. | `src/albra_seed/runtime/agent.py:167` (`Agent.run` signature includes `token: str \| None = None`); `src/albra_seed/runtime/agent.py:384` (`ctx_dict["token"] = token`); `src/albra_seed/runtime/agent.py:1085` (`resolver.resolve_and_attach(run_ctx, token=token)`). | `tests/security/test_adversarial.py::TestTokenValidation::test_valid_token_authorizes`; `tests/security/test_adversarial.py::TestTokenValidation::test_invalid_token_denies`; `tests/security/test_adversarial.py::TestTokenValidation::test_expired_token_denies`; `tests/security/test_adversarial.py::TestTokenValidation::test_no_token_denies_protected_action`. | PASS |
| **H-04** | `RunContext.verified_principal` is `Any` (typed loosely to break the circular import with `security.auth`); consumers use `getattr(vp, "roles", [])` for None-robust access. | `src/albra_seed/core/types.py:312` (`verified_principal: Any = None`). | `tests/security/test_rbac_security.py::TestIdentityResolver::test_verified_principal_not_in_metadata`; `tests/security/test_adversarial.py::TestForgedPrincipal::test_forged_verified_principal_rejected`. | PASS |
| **H-05** | Cognitive-loop budget pre-check — `max_iterations <= 0` short-circuits to RESPOND without entering the cognitive loop; emits `cognitive_budget_exhausted` warning. | `src/albra_seed/runtime/agent.py:578` (budget pre-check); `src/albra_seed/runtime/agent.py:586` (post-loop phases still run). | `tests/unit/test_cognitive_cycle.py::test_default_cognitive_loop_config_has_safe_limits` (smoke); `tests/unit/test_cognitive_modes.py::TestCognitiveModeDefaults::test_cognitive_mode_respects_max_iterations`. | PASS |
| **H-06** | Cognitive-loop time budget — `max_time_seconds` elapsed → loop terminates with `cognitive_loop_limit_reached:max_time_seconds`; per-call `cognitive_remaining_seconds` checked before every dispatch. | `src/albra_seed/runtime/agent.py:774` (between-iterations check); `src/albra_seed/runtime/agent.py:1410` (per-call check in `_phase_execute`). | `tests/unit/test_cognitive_cycle.py::test_max_time_limit`; `tests/security/test_adversarial.py::TestTimeout::test_cognitive_time_budget_exhausted_blocks`. | PASS |
| **H-07** | Per-tool cap — `max_calls_per_tool` (default 5) prevents a single tool name from being invoked more than that many times across all iterations; exceeding it produces a `per_tool_call_limit_exceeded` ToolResult. | `src/albra_seed/runtime/agent.py:1426` (per-tool cap check in `_phase_execute`); `src/albra_seed/runtime/loop.py:94` (`CognitiveLoopConfig.max_calls_per_tool: int = 5`). | `tests/unit/test_cognitive_cycle.py::test_max_tool_calls_limit` (raises per-tool cap above the total cap to verify only the total cap binds); `tests/security/test_adversarial.py::TestCallExcess::test_per_tool_call_limit_exceeded_blocks`. | PASS |
| **H-08** | Idempotent `call_id` — a second `executor.execute(..., call_id=X)` with the SAME `call_id` returns a successful no-op (`output.idempotent_skip=True`); the underlying tool's `execute()` is NOT re-invoked. | `src/albra_seed/tools/executor.py:165` (idempotency gate); `src/albra_seed/tools/executor.py:215` (record after first execution). | `tests/security/test_adversarial.py::TestRetry::test_idempotent_call_id_not_re_executed`; `tests/security/test_adversarial.py::TestDirectExecutorBypass::test_tool_executor_with_allowed_authorization_executes` (first-call sanity). | PASS |
| **H-09** | Cognitive mode defaults — the Agent has TWO documented modes: **LINEAR** (default, `cognitive_loop_config=None`) for a single REASON→EXECUTE→RESPOND cycle with no re-reasoning, and **COGNITIVE** (opt-in via `CognitiveLoopConfig()`) for iterative tool-reasoning with budget limits. The per-call `Agent.run(cognitive_loop_config=...)` is OPT-IN only — passing `None` falls back to the construction-time config. | `src/albra_seed/runtime/agent.py:121` (`Agent.__init__` signature); `src/albra_seed/runtime/agent.py:167` (`Agent.run` signature); `src/albra_seed/runtime/agent.py:365` (per-call override resolution); `src/albra_seed/runtime/agent.py:553` (LINEAR branch); `src/albra_seed/runtime/agent.py:566` (COGNITIVE branch). | `tests/unit/test_cognitive_modes.py::TestCognitiveModeDefaults::test_default_mode_is_linear`; `tests/unit/test_cognitive_modes.py::TestCognitiveModeDefaults::test_cognitive_mode_iterates`; `tests/unit/test_cognitive_modes.py::TestCognitiveModeDefaults::test_cognitive_mode_respects_max_iterations`; `tests/unit/test_cognitive_modes.py::TestCognitiveModeDefaults::test_linear_mode_denied_tool_still_responds`; `tests/unit/test_cognitive_modes.py::TestCognitiveModeDefaults::test_cognitive_mode_denied_tool_still_iterates`; `tests/unit/test_cognitive_modes.py::TestBothModesProduceValidResponses`; `tests/unit/test_cognitive_modes.py::TestBothModesRespectAuthorization`; `tests/unit/test_cognitive_modes.py::TestCognitiveModePerCallOverride::test_run_can_override_to_cognitive`; `tests/unit/test_cognitive_modes.py::TestCognitiveModePerCallOverride::test_run_with_none_falls_back_to_construction_config`; `tests/unit/test_cognitive_modes.py::TestModeSelectionInvariant` (3 tests). | PASS |
| **H-10** | Adversarial tests — every security finding has a test that verifies BOTH the decision (DENY/ALLOW) AND the effect (tool.execute() invoked or NOT, no side-effect, no authorization change). | `tests/security/test_adversarial.py` (21 tests across 9 classes). | `tests/security/test_adversarial.py::TestTokenValidation` (4 tests); `TestForgedPrincipal` (2 tests); `TestDirectExecutorBypass` (4 tests); `TestRevocationBetweenCheckAndExecute` (2 tests); `TestContractPermissions` (1 test); `TestCallExcess` (2 tests); `TestTimeout` (2 tests); `TestRetry` (1 test); `TestNoEffectOnDenial` (3 tests). | PASS |
| **H-11** | Documentation sync — `MASTER_DNA.md`, `CERTIFICATION.md`, `README.md`, and this matrix are updated to reflect the final state after H-01..H-10. | `docs/MASTER_DNA.md` (§B "Cognitive cycle" + new §B "Central tool executor" + §E "H-09/H-10/H-11 amendments"); `docs/CERTIFICATION.md` (numbers updated to 769; new "Post-V1.0 hardening amendments" table); `README.md` (test counts + new bullet for central executor + cognitive modes); `docs/REQUIREMENTS_MATRIX.md` (this file). | Manual review; the numbers in those docs match `python -m pytest tests/ --collect-only -q` → 769 tests; the test-file references in this matrix point at tests that actually exist and pass. | PASS |

---

## Verification protocol

To re-verify every row in this matrix:

```bash
cd /path/to/agent-general

# 1. Run the full test suite — every test referenced in the table
#    above MUST pass.
python -m pytest tests/ -q
#    → expected: 769 passed

# 2. Run only the H-09 + H-10 test files (the new ones added by
#    this amendment).
python -m pytest tests/unit/test_cognitive_modes.py -v
#    → expected: 16 passed

python -m pytest tests/security/test_adversarial.py -v
#    → expected: 21 passed

# 3. Spot-check the central-executor smoke test (H-01/H-02/H-08).
python -m pytest tests/unit/test_cognitive_cycle.py -v
#    → expected: 15 passed (existing cognitive-cycle tests)

# 4. Spot-check the existing RBAC security perimeter (H-03/H-04).
python -m pytest tests/security/test_rbac_security.py -v
#    → expected: 31 passed
```

If any of the above fails, the corresponding row in the matrix must
be marked `N/A` (not yet verified) until the failure is fixed.

---

## Cross-references

- [`docs/MASTER_DNA.md`](MASTER_DNA.md) — the canonical contract
  every ALBRA organism MUST satisfy. Section B "Cognitive cycle" +
  "Central tool executor" record the H-01/H-02/H-08/H-09 contract.
  Section E "H-09/H-10/H-11 amendments" documents this amendment.
- [`docs/CERTIFICATION.md`](CERTIFICATION.md) — the canonical single
  source of truth for all numbers and gate results. Updated to 769
  tests with the "Post-V1.0 hardening amendments (H-01..H-11)"
  sub-table.
- [`README.md`](../README.md) — Quick Start + Project Status updated
  with the central executor + cognitive modes bullet.

---

**PRINCIPLE MAESTRO:**
> *Every requirement has a code location AND a proving test. A
> requirement without a test is NOT a requirement — it is a wish.*
