# ALBRA AGENT SEED V1.0 — Universal DNA Gap Closure Report

> **Document ID:** `UNIVERSAL_DNA_GAP_CLOSURE.md`
> **Generated:** 2026-10-11 by Task ID `GAP-DOCS`
> **Scope:** Closure of the 7 audit gaps identified against the ALBRA SEED V1.0 universal DNA.
> **Authority:** This report is the single canonical record of the gap-closure effort. All claims
> here are reproducible by running `python scripts/audit_matrix.py`, `python scripts/parity_check.py`,
> `python scripts/contamination_scan.py`, and `python -m pytest tests/ -q` against
> `/home/z/my-project/agent-general/`.

---

## Executive Summary

This report documents the closure of **7 gaps** identified in the audit of the
ALBRA AGENT SEED V1.0 universal DNA. **All gaps were closed by COMPOSING with
existing components, NOT by duplicating or replacing them.**

**Final Status:** `DEVELOPMENT COMPLETE / INTEGRATION READY / UNIVERSAL DNA COMPLETE`

| Gate                          | Result                                                      |
|---|---|
| Tests                         | **372 PASS** (was 280 baseline; +92 new; 0 regressions)    |
| Clean-room                    | **PASS** (16/16 + 1 optional)                               |
| Genetic Parity                | **PASS** (98/98 DNA components, `is_pure_seed=True`)        |
| Genetic Contamination         | **PASS** (0 critical findings in 236 files, `is_clean=True`) |
| Audit Matrix                  | **39/39 VERIFIED** (was 33 + 6 new gap-closure probes)     |
| CORE_ONLY birth              | **PASS** (Mock provider auto-registered at birth)           |

---

## Per-Gap Closure

### GAP-1: Universal Feedback

1. **GAP**: No unified feedback mechanism. Outcome tracking was fragmented —
   every subsystem tracked success/failure its own way, with no canonical
   record that could be queried, summarized, or correlated across runs.
2. **SOLUTION**: New `feedback/` package with `FeedbackManager` +
   `FeedbackStore` (ABC + InMemory + SQLite) + `FeedbackType` (6 domain-agnostic
   values: `POSITIVE`, `NEGATIVE`, `CORRECTION`, `HUMAN_FEEDBACK`,
   `SYSTEM_FEEDBACK`, `AUTOMATED_EVALUATION`) + `FeedbackRecord` (with
   `execution_id`, `correlation_id`, `agent_id`, `expected_outcome`,
   `actual_outcome`, `evaluation`, `source`/`source_type`, `evidence_ids`,
   `decision_id`, `skill_id`, `provenance_id`).
3. **FILES MODIFIED**: `src/albra_seed/__init__.py` (4 new exports + 4 `__all__`
   entries only — fully CORE_ONLY-safe, no external deps pulled in).
4. **COMPONENTS REUSED**: `CorrelationTracker` (auto-correlation from current
   execution context — same pattern as `audit/normal.py:AuditLog.record`),
   `ProvenanceTracker` (best-effort attachment via `record_kind="feedback"` —
   literal string, no new KIND constant needed).
5. **TESTS**: 17 tests in `tests/unit/test_feedback.py` (record/get/query/
   summary/auto-correlate/link-to-decision/sqlite-persist/6-type-distinguish/
   never-confused-with-evidence + 2 bonus provenance tests).
6. **RESULT**: **PASS** — feedback records can be created, queried, summarized
   by type, and linked to decisions via `decision_id`. The ID prefix `fb_`
   is distinct from `ev_`/`aud_`/`dec_`/`learn_`/`prov_` so stores can never
   be confused (verified by `test_feedback_never_confused_with_evidence`).
7. **RISKS**: None — purely additive. No existing source files modified
   except `src/albra_seed/__init__.py` (export-only).
8. **LIMITATIONS**: Feedback store doesn't auto-cleanup; no retention policy
   (intentional — domain-specific decision).

### GAP-2: Universal Learning

1. **GAP**: No universal mechanism to convert observations (feedback,
   decision outcomes, execution traces) into actionable improvement proposals
   with a human-in-the-loop gate. AutonomyLevel.L5_SELF_IMPROVE existed in
   `core/types.py` but was unused.
2. **SOLUTION**: New `learning/` package with `LearningManager` +
   `LearningStore` (ABC + InMemory + SQLite) + `LearningProposal` model +
   `LearningSignal` enum (4 values: `FEEDBACK`/`OUTCOME`/`DECISION`/`EXPERIENCE`)
   + `ProposalStatus` enum (6 values: `PROPOSED`→`REVIEW_REQUIRED`→`APPROVED`→
   `APPLIED` / `REJECTED` / `SUPERSEDED`) + `ProposalGenerator` (3 async
   builders: `from_feedback`, `from_decision_outcome`, `from_execution_trace`).
3. **FILES MODIFIED**: `src/albra_seed/__init__.py` (6 new exports + 6
   `__all__` entries only).
4. **COMPONENTS REUSED**: `FeedbackManager` (GAP-1), `DecisionMemory` (GAP-3),
   `ExecutionTrace` (F2-F), `CorrelationTracker`, `ProvenanceTracker`
   (best-effort, `record_kind="learning_proposal"`). Lazy `TYPE_CHECKING`
   imports for `FeedbackManager`/`DecisionMemory`/`ExecutionTrace` avoid
   any circular-import risk. HITL pattern mirrors `approvals/engine.py`.
5. **TESTS**: 12 tests in `tests/unit/test_learning.py` (propose/get/full
   pipeline/reject-reason/apply-without-approval-raises/apply-rejected-raises/
   supersede/from_feedback/from_decision_outcome/provenance/sqlite + bonus
   from_execution_trace).
6. **RESULT**: **PASS** — proposals flow PROPOSED → REVIEW_REQUIRED → APPROVED
   → APPLIED. `apply()` raises `LearningError` unless `status == APPROVED`.
7. **RISKS**: HIGH (correctness-critical lifecycle state machine) — mitigated
   by 4 dedicated state-transition tests.
8. **LIMITATIONS**: **Learning is NOT ML training.** It's a proposal pipeline.
   `apply()` **NEVER auto-modifies code, skills, memory, or tools** — it only
   records that an approved proposal was externally applied. The actual
   change happens outside the Seed (human/dev action); the Learning system
   records the lifecycle.

### GAP-3: Decision Memory

1. **GAP**: Decisions were captured only implicitly via `AuditLog` events
   and `ExecutionTrace` reasoning. There was no first-class record of
   "this agent, faced with this objective, chose this option, for this
   reason" — and no way to link an outcome back to a decision.
2. **SOLUTION**: New `memory/decisions.py` (`DecisionRecord` structured
   wrapper) + `memory/decision_store.py` (`DecisionMemory` thin adapter).
   `DecisionRecord` carries: `decision_id` (= `record_id`), `agent_id`,
   `context_id`, `objective`, `options_considered`, `decision_taken`,
   `reasoning`, `evidence_ids`, `execution_id`, `correlation_id`,
   `outcome`, `feedback_ids`, `confidence`, `timestamp`. It round-trips
   through `MemoryRecord(memory_type=MemoryType.DECISION)` losslessly.
3. **FILES MODIFIED**:
   - `src/albra_seed/core/types.py` (appended `DECISION = "decision"` as
     the 6th `MemoryType` enum value AFTER the original 5 — non-breaking).
   - `src/albra_seed/memory/__init__.py` (exports `DecisionRecord`,
     `DecisionMemory`).
   - `src/albra_seed/__init__.py` (eager imports for CORE_ONLY safety).
4. **COMPONENTS REUSED**: `MemoryStore` ABC + `InMemoryMemoryStore` +
   `SQLiteStore` (no new store — same store, same isolation rules, same
   provenance discipline), `ProvenanceTracker` (best-effort
   `track_decision`).
5. **TESTS**: 10 tests in `tests/unit/test_decision_memory.py`
   (record-and-get/list-by-agent/domain-isolation/context+user-isolation/
   link-outcome/reuses-memorydv-isolation/preserves-provenance/ +
   non-decision-record-TypeError + 5-original-values-still-present).
6. **RESULT**: **PASS** — `DecisionMemory` is a SIBLING of `MemoryManager`,
   not a parallel system. A `MemoryManager` pointing at the same
   `InMemoryMemoryStore` sees `DECISION` records via `recall()` (no
   `memory_type` filter); `DecisionMemory.list_decisions()` filters to
   `DECISION` only. Verified by `test_decision_memory_reuses_memorydv_isolation`.
7. **RISKS**: None — non-destructive extension of MemoryDV.
8. **LIMITATIONS**: **NON-destructive.** `MemoryType.DECISION` is purely
   additive (the 6th value). `DecisionMemory` is a thin adapter over the
   existing `MemoryStore`, NOT a parallel system.

### GAP-4: Skill Evolution

1. **GAP**: Skills had a registry + lifecycle + executor but no way to
   capture execution metrics (success rate, failure count, latency) and
   convert them into improvement proposals. Skills could not "evolve" —
   they could only be replaced.
2. **SOLUTION**: New `skills/evolution.py` with `SkillMetrics` (per-skill
   aggregated execution/feedback stats, rolling incremental avg),
   `ImprovementProposal` (ID prefix `skillprop_`), `SkillEvolutionManager`
   (async, in-memory; tracks metrics + proposals; HITL lifecycle mirroring
   GAP-2 `LearningManager`), `SkillEvolutionError`. Modified
   `skills/executor.py` to call `_record_evolution()` after each execute
   path (success/timeout/handler-exception) via optional
   `evolution_manager` constructor arg.
3. **FILES MODIFIED**:
   - `src/albra_seed/skills/__init__.py` (4 new exports).
   - `src/albra_seed/__init__.py` (4 new eager imports).
   - `src/albra_seed/skills/executor.py` (optional constructor arg + best-effort
     `_record_evolution()` hook — never breaks skill execution on manager
     failure).
4. **COMPONENTS REUSED**: Existing `SkillRegistry.upgrade_version()` for
   version bumps, `SkillLifecycle.upgrade_version()` transitively,
   `FeedbackManager` (GAP-1, constructor arg kept for composability).
   The `ImprovementProposal.source_learning_proposal_id` links to GAP-2
   `LearningProposal` (target_component="skill") without importing the
   learning subpackage (loose coupling).
5. **TESTS**: 16 tests in `tests/unit/test_skill_evolution.py`
   (metrics-defaults/success-rate/record-execution/record-feedback/
   propose-improvement/full-pipeline/reject/apply-without-approval-raises/
   apply-rejected-raises/apply-upgrades-version/metrics-snapshot/
   does-not-modify-skill-code/apply-without-version-skips-upgrade/
   list-filters/executor-records-metrics/executor-without-manager-regression).
6. **RESULT**: **PASS** — apply() delegates to `SkillRegistry.upgrade_version()`
   ONLY when `proposed_version` is set; never calls `register`/`disable`/
   `enable`/`remove`. Verified by
   `test_skill_evolution_does_not_modify_skill_code_automatically`.
7. **RISKS**: None — extends existing `SkillRegistry`, does not replace it.
8. **LIMITATIONS**: **EXTENDS existing `SkillRegistry` (not replaces).**
   `apply()` requires `APPROVED` status (HITL gate). The ONLY mutation it
   MAY perform on the registry is `upgrade_version()` — and ONLY when
   `proposed_version` is not `None`. No skill handler code is auto-modified.

### GAP-5: Birth / Readiness Contract

1. **GAP**: `Runtime.startup()` did NOT auto-register `provider_router` —
   the caller had to `runtime.register("provider_router", router)` manually
   for `readiness()` to return `True`. This broke the CORE_ONLY birth
   contract: the Seed could not be "born" without manual orchestration.
2. **SOLUTION**: Modified `runtime/runtime.py`:
   - Added `_bootstrap_source: str | None` field (records "auto" vs "caller").
   - `startup()` now auto-creates `ProviderRouter()` (which auto-creates a
     `ProviderRegistry` with the always-on Mock provider) when
     `provider_router` is NOT already in `self._components`. **Caller's
     explicit registration ALWAYS wins** — bootstrap only fills an empty
     slot. Emits `runtime.bootstrap.provider_router` log event with
     `source="auto"`, `mock=True`. Failures are caught and logged via
     `runtime.bootstrap.provider_router_failed` (never crashes startup).
   - Added `async def birth(self) -> dict` method: runs `startup()` if
     UNINITIALIZED; auto-registers `InMemoryMemoryStore` and `AuditLog`
     if missing; verifies identity carries `seed_version`; verifies
     `readiness()`; verifies Mock reachable via router; executes a no-op
     `Agent.run("birth")` task; writes+reads a memory record; appends an
     audit entry. Returns snapshot dict with: `identity`, `seed_version`,
     `runtime_ready`, `phase`, `mock_available`, `agent_loop_ok`,
     `memory_ok`, `audit_ok`, `external_providers`, `bootstrap_source`,
     `born_at`.
   - Modified `runtime/agent.py::_phase_reason()` to prefer
     `runtime._providers` over the router (resolution order: explicit
     `register_provider()` first → `provider_router` (auto-registered at
     startup) → no-op draft). This preserves all existing test contracts
     (custom providers via `register_provider()` still take precedence)
     while enabling CORE_ONLY birth.
3. **FILES MODIFIED**:
   - `src/albra_seed/runtime/runtime.py` (auto-bootstrap + `birth()` method).
   - `src/albra_seed/runtime/agent.py` (provider resolution order).
   - `tests/unit/test_runtime.py::test_runtime_readiness_requires_components`
     (updated to reflect auto-registration — caller's explicit registration
     still wins).
   - `docs/BIRTH_CONTRACT.md` (new — documents the birth contract + snapshot
     semantics + provider resolution order).
   - `tests/unit/test_birth_readiness.py` (new — 8 tests).
4. **COMPONENTS REUSED**: `ProviderRouter` + `ProviderRegistry` (unchanged),
   `Mock` provider (the always-on birth default), `InMemoryMemoryStore`,
   `AuditLog`. No new providers were added.
5. **TESTS**: 8 new tests in `tests/unit/test_birth_readiness.py`
   (auto-registers-provider-router-on-startup/
   does-not-overwrite-caller-provider-router/
   readiness-true-in-core-only/birth-returns-snapshot/
   birth-agent-can-run-task/birth-no-external-providers/
   birth-logs-bootstrap-event/birth-idempotent).
6. **RESULT**: **PASS** — `Runtime()` → `await runtime.startup()` →
   `runtime.readiness() == True`. No external providers required. No manual
   registration required. `Runtime.birth()` snapshot in CORE_ONLY mode has
   all `*_ok == True` and `external_providers == []`.
7. **RISKS**: None — surgical fix. `REQUIRED_FOR_READINESS` unchanged
   (`("provider_router",)`); ProviderRegistry and ProviderRouter unchanged;
   Mock stays the fallback; caller's explicit registration always wins.
8. **LIMITATIONS**: **`Runtime.startup()` now auto-registers
   `ProviderRouter` (with Mock) if not already registered. Caller's
   explicit registration always wins.** CORE_ONLY birth now works
   out-of-the-box. This is a behavior change in `startup()` — non-breaking
   because caller's registration takes precedence.

### GAP-6: Genetic Parity Test

1. **GAP**: No automated way to verify that a specialized ALBRA agent
   preserves the universal DNA of the Seed. A specialization could
   accidentally remove a DNA component (e.g., the kill switch) and no test
   would fail.
2. **SOLUTION**: New `parity/` package with `ParityChecker` +
   `ParityReport` dataclass + `UNIVERSAL_DNA_COMPONENTS` catalog (98
   entries, every entry maps to an importable `albra_seed.<component>`
   module) + `ALLOWED_SPECIALIZATIONS` (8 entries). 15 check methods:
   `check_imports`, `check_classes` (15 key public classes), `check_runtime_birth`,
   `check_agent_loop`, `check_memory_dv`, `check_audit`, `check_evidence`,
   `check_firewall_default_deny`, `check_kill_switch`, `check_doctor`,
   `check_feedback` (GAP-1), `check_learning` (GAP-2),
   `check_decision_memory` (GAP-3), `check_skill_evolution` (GAP-4),
   `detect_specialization_components` (scans `sys.modules` for any
   `specialization.*` module NOT in `ALLOWED_SPECIALIZATIONS` — never
   imports anything itself), `run_all` (aggregates into `ParityReport`).
3. **FILES MODIFIED**: None. Purely additive — new `parity/` package +
   new test directory + new CLI script.
4. **COMPONENTS REUSED**: All 33+ existing Seed components are imported
   on-demand (inside check methods, never at module load) so CORE_ONLY
   mode remains intact. No domain knowledge of its own.
5. **TESTS**: 17 tests in `tests/genetic/test_parity.py` (15 required +
   2 bonus: DNA catalog well-formed + report `to_dict()` shape).
6. **RESULT**: **PASS** — Seed is verified pure: 98/98 DNA components
   present, 13/13 behavioral checks pass, 0 specialization components
   detected, `is_pure_seed=True`. Verified by both
   `test_parity_report_is_pure_seed` AND the CLI:
   `python scripts/parity_check.py` exits 0.
7. **RISKS**: LOW — catalog is a pure data file; scanner has no side effects.
8. **LIMITATIONS**: 98 universal DNA components cataloged.
   `ParityChecker.run_all()` verifies all are importable + behavioral
   checks pass. `is_pure_seed=True` for the Seed itself. A specialization
   MAY add components in `specialization.*` (those in
   `ALLOWED_SPECIALIZATIONS` are recognised as benign); anything else in
   `specialization.*` flips `is_pure_seed` to `False` (but does NOT cause
   parity failure, because additions are permitted — only REMOVALS or
   REPLACEMENTS of DNA components fail).

### GAP-7: Genetic Contamination Test

1. **GAP**: No automated way to detect if domain-specific knowledge
   (legal, CRM, trading, YouTube automation, etc.) had leaked into the
   universal Seed. The Seed MUST remain domain-agnostic.
2. **SOLUTION**: New `contamination/` package with `ContaminationScanner`
   + `ContaminationReport` + `ContaminationFinding` (pydantic v2 models) +
   `FORBIDDEN_DOMAIN_KEYWORDS` (24 entries: legal/lawyer/abogado*/juridic*/
   crm/customer_relationship/krea/leads/lead_gen/trading/trader/youtube/
   yt_automation/secop*/radar_secop/chismoso + concept words like
   contract_drafting/sales_pipeline/marketing_campaign/financial_advice/
   investment_recommendation) + `FORBIDDEN_CODE_PATTERNS` (4 regexes:
   `import`/`from-import` of forbidden modules, hardcoded `domain = "..."`,
   forbidden class-name pattern) + `ALLOWED_PATHS` (docs/, README.md, the
   contamination package itself, tests, scripts, plus 3 seed files whose
   docstrings mention a domain only to assert "no domain here") +
   `SCANNABLE_EXTENSIONS` ({.py, .txt, .md, .yaml, .yml, .json, .toml}).
3. **FILES MODIFIED**: None. Purely additive — 3 new src files +
   tests/contamination/ + scripts/contamination_scan.py.
4. **COMPONENTS REUSED**: `pathlib` only — no Seed internals. Scanner is
   a sibling of `parity/`; together they form the ALBRA Genetic Integrity
   Suite (parity = positive contract "all DNA present"; contamination =
   negative contract "no domain leaked").
5. **TESTS**: 12 tests in `tests/contamination/test_contamination.py`
   (finds-keyword-in-py/skips-allowed-paths/skips-pycache/finds-import-pattern/
   finds-class-name-pattern/finds-hardcoded-domain/whole-word-match/
   case-insensitive/clean-seed/pydantic-model-shape/
   critical-vs-warning/catalog-well-formed).
6. **RESULT**: **PASS** — 236 files scanned, 0 critical findings, 0
   warning findings, `is_clean=True`. Verified by both
   `test_scanner_clean_seed` AND the CLI:
   `python scripts/contamination_scan.py` exits 0.
7. **RISKS**: LOW — scanner is read-only, never mutates files.
8. **LIMITATIONS**: 24 forbidden domain keywords + 4 forbidden code
   patterns. Scanner skips `ALLOWED_PATHS` (docs, tests, scripts). Seed
   scan: 236 files, 0 critical findings, `is_clean=True`. The 3 seed
   source files whose docstrings mention a domain name only to assert
   "no domain here" are listed in `ALLOWED_PATHS` (not modified) per the
   task's explicit rule: "fix the keyword list or allowed paths, NOT
   the existing Seed code".

---

## Composition Summary (NOT Duplication)

The cardinal rule of gap closure: **compose with existing components, never
duplicate or replace.** Every new component below either imports an existing
component at construction time or mirrors an existing pattern.

| New Component       | Reuses                                                                | Pattern from                       |
|---|---|---|
| `feedback`          | `CorrelationTracker`, `ProvenanceTracker` (best-effort)               | `audit/normal.py` + `evidence/manager.py` |
| `learning`          | `FeedbackManager`, `DecisionMemory`, `ExecutionTrace`                 | `approvals/engine.py` (HITL pattern) |
| `decision_memory`   | `MemoryStore`, `MemoryRecord`, `ProvenanceTracker`                   | `memory/manager.py` |
| `skill_evolution`   | `SkillRegistry`, `SkillLifecycle`, `FeedbackManager`                 | `skills/lifecycle.py` |
| `parity`            | All 33+ existing components (lazy import inside check methods)        | — |
| `contamination`     | `pathlib` only                                                        | — |

---

## What Did NOT Change

- ✅ All **33 existing audit-matrix components** unchanged (no probe modified)
- ✅ All **280 original tests** still pass (verified by `pytest tests/ -q` → 372)
- ✅ **Clean-room verification** still PASS (16/16 + 1 optional)
- ✅ **Mock provider** remains the birth default
- ✅ **CORE_ONLY mode** preserved (`SeedConfig.is_core_only` still works)
- ✅ **API and CLI** unchanged
- ✅ **No domain knowledge** added (contamination scan: 0 critical findings)
- ✅ All existing source files preserved (the only files modified were
  `src/albra_seed/__init__.py` for new exports, `src/albra_seed/core/types.py`
  to add the 6th `MemoryType.DECISION` value, `src/albra_seed/skills/__init__.py`
  for new exports, `src/albra_seed/skills/executor.py` for the optional
  evolution hook, `src/albra_seed/runtime/runtime.py` for auto-bootstrap +
  `birth()`, and `src/albra_seed/runtime/agent.py` for provider resolution order)

## What WAS Added (Purely Additive)

- **6 new subpackages**: `feedback/`, `learning/`, `contamination/`, `parity/`
  (4 new packages) + 2 new modules in existing packages:
  `memory/decisions.py`, `memory/decision_store.py`, `skills/evolution.py`
- **1 enum value added**: `MemoryType.DECISION` (non-breaking — 6th value
  after the original 5)
- **1 method added to Runtime**: `birth()` (non-breaking, additive)
- **1 behavior change in Runtime.startup()**: auto-register
  `ProviderRouter` if missing (non-breaking — caller's registration wins)
- **1 optional constructor arg** in `SkillExecutor`: `evolution_manager`
  (default `None`; backward-compatible)
- **92 new tests** (372 total, was 280 baseline)
- **6 new probes in audit_matrix.py** (39 total, was 33)
- **2 new CLI scripts**: `scripts/parity_check.py`,
  `scripts/contamination_scan.py`
- **1 new doc**: `docs/BIRTH_CONTRACT.md`
- **This report**: `docs/UNIVERSAL_DNA_GAP_CLOSURE.md`

---

## Reproducing This Report

All claims in this report are reproducible by running the following commands
from `/home/z/my-project/agent-general/`:

```bash
# Audit matrix (39 components probed — 33 original + 6 gap-closure)
python scripts/audit_matrix.py 2>&1 | tail -3
# Expected: Total components: 39 / Status counts: {'VERIFIED': 39}

# Genetic parity (98 DNA components, 13 behavioral checks)
python scripts/parity_check.py
# Expected: exit 0, is_pure_seed=True, 98/98 DNA present, 13/13 checks pass

# Genetic contamination (236 files, 0 critical findings)
python scripts/contamination_scan.py
# Expected: exit 0, is_clean=True, 0 critical findings, 236 files scanned

# Full test suite (372 tests, 0 regressions)
python -m pytest tests/ -q
# Expected: 372 passed in ~7s
```

---

## Final Status

```
DEVELOPMENT_COMPLETE
INTEGRATION_READY
UNIVERSAL_DNA_COMPLETE
```

The ALBRA AGENT SEED V1.0 universal DNA is **closed**: every audit gap has
been addressed by composition, every gate passes, every test is green, and
the Seed remains domain-agnostic. Specialized ALBRA agents may now be built
on top of the Seed with the guarantee that:
1. The 98 universal DNA components are preserved (GAP-6).
2. No domain knowledge has leaked into the Seed (GAP-7).
3. CORE_ONLY birth works out-of-the-box (GAP-5).
4. The feedback → learning → decision → evolution loop is in place for
   outcome-driven improvement (GAP-1/2/3/4) with HITL gates throughout.
