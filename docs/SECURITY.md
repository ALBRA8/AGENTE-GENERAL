# ALBRA AGENT SEED V1.0 — Security Model

> La seguridad de la semilla no es opcional. El Seed es ADN transversal: cada
> agente ALBRA especializado hereda este modelo sin poder saltárselo.
>
> This document covers every security primitive shipped by the seed and the
> invariants each one enforces. None of these primitives may be disabled by a
> specialization layer.

---

## 1. Threat Model

The seed assumes three classes of threats:

| Threat                  | Description                                                                                  | Mitigation                                                                  |
| ----------------------- | -------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Unauthorized action      | Agent executes a tool/call that was never explicitly allowed.                                 | ActionFirewall (DEFAULT-DENY) + PolicyEngine + RBAC PermissionRegistry.    |
| Excessive autonomy      | Agent decides on its own to perform a sensitive action that should require a human.          | AutonomyMatrix + AutonomyProfile + HITL ApprovalEngine.                     |
| Audit tampering         | Operator or process attempts to silently rewrite historical audit records.                  | Tamper-EvidentAuditLog (hash-chained, separate from normal audit).          |
| Secret leakage          | Secret values printed in logs / responses / error messages.                                  | SecretsManager + StructuredLogger redaction + SecretsAdapter.               |
| Evidence forgery        | Unknown / inference-tier evidence is auto-promoted to FACT to bypass HITL.                   | EvidenceContract (UNKNOWN never auto-promotes; FACT requires ≥0.9 confidence). |
| Cascade failure         | One failing component triggers a destructive auto-recovery (e.g. wipes a DB).                | RecoveryManager refuses SECURITY/UNKNOWN classes + dangerous markers.       |
| Runaway external action | A misconfigured provider or runaway loop hammers an external API.                            | KillSwitch (global) + RateLimiter + CooldownManager.                        |

Every primitive below is exercised by a security test under `tests/security/`.

---

## 2. Action Firewall

**Location**: `albra_seed/firewall/firewall.py`

The ActionFirewall is the single gate every external action MUST pass through.

### 2.1 DEFAULT-DENY

If no rule explicitly matches an action, the firewall returns
`PolicyDecision.DENY`. There is no "implicit allow" code path.

```python
from albra_seed.firewall.firewall import ActionFirewall

firewall = ActionFirewall()                      # no rules
verdict = await firewall.check("totally_unknown_action")
assert verdict.allowed is False                  # default-deny
assert verdict.decision.value == "deny"
assert verdict.reason == "no_matching_rule"
```

### 2.2 Rule evaluation order

Evaluation is deterministic and short-circuits:

```
1. Kill switch active?      → DENY  (kill_switch_active=True)
2. Cooldown active?         → DENY  (cooldown_remaining_seconds=N)
3. Rate limit exceeded?     → DENY  (rate_limited=True)
4. First matching rule      → rule.decision (ALLOW / REQUIRE_HITL / DRY_RUN / DENY)
5. No rule matched          → DENY  (reason="no_matching_rule")
```

The first matching rule (by priority descending) wins. Rules of equal priority
are evaluated in insertion order.

### 2.3 FirewallRule fields

| Field                  | Type              | Purpose                                                       |
| ---------------------- | ----------------- | ------------------------------------------------------------- |
| `action_pattern`       | glob string       | `"tool.http.*"` matches any HTTP tool invocation.            |
| `actor_pattern`        | glob string       | `"agent:albra-*"` restricts to a class of agents.            |
| `risk_threshold`       | `RiskLevel`       | Rule applies only when `context.risk_level >= threshold`.     |
| `autonomy_required`    | `AutonomyLevel`   | Actor autonomy must be `>=`.                                  |
| `decision`             | `PolicyDecision`  | ALLOW / DENY / REQUIRE_HITL / DRY_RUN.                        |
| `cooldown_seconds`     | int               | Per-action cooldown enforced after an ALLOW.                  |
| `rate_limit`           | int               | Max invocations per `rate_window_seconds`.                    |
| `dry_run_capable`      | bool              | Whether the action can be executed in dry-run mode.           |
| `priority`             | int               | Higher = evaluated first.                                     |

### 2.4 Verdict

`ActionFirewall.check()` returns a `FirewallVerdict` with:
- `allowed: bool` — **True ONLY when decision == ALLOW**. `REQUIRE_HITL` and `DRY_RUN`
  are NOT allowed; the caller MUST handle them explicitly.
- `decision: PolicyDecision`
- `reason: str`
- `matched_rule_id: str | None`
- `kill_switch_active: bool`
- `cooldown_remaining_seconds: int`
- `rate_limited: bool`

---

## 3. Policy Engine

**Location**: `albra_seed/policy/engine.py`

The PolicyEngine composes four gates and produces a final `PolicyVerdict`:

```
action + actor + risk + permission + context + autonomy
                        ↓
                    PolicyEngine.evaluate()
                        ↓
   pre-check permission   ──→  missing?  →  DENY
   pre-check autonomy     ──→  insufficient? → REQUIRE_HITL
   rule evaluation        ──→  first-match-wins
   ALLOW + post-checks    ──→  finalize ALLOW / REQUIRE_HITL / DENY
```

### 3.1 The six policy inputs

The engine consults all six inputs before reaching a verdict:

| Input           | Source                                  | Effect on verdict                                                |
| --------------- | --------------------------------------- | ---------------------------------------------------------------- |
| **action**      | `ToolCall.tool_name` or string          | Matched against `PolicyRule.action_pattern` (glob).              |
| **actor**       | `actor_id` kwarg or `context.agent_id`  | Matched against `actor_pattern` + looked up in permission registry. |
| **risk**        | `RunContext.risk_level`                 | Rule applies only if `risk >= rule.risk_threshold`.              |
| **permission**  | `PermissionRegistry.has_any(roles, perm)` | If a rule requires a permission and the actor's roles don't grant it → DENY. |
| **context**     | `RunContext` (execution_id, correlation_id, autonomy_level) | Carried for audit + autonomy check. |
| **autonomy**    | `AutonomyManager.can_perform(current, required)` | If insufficient → REQUIRE_HITL (even if rule says ALLOW). |

### 3.2 Decisions

| Decision           | Meaning                                                                |
| ------------------ | ---------------------------------------------------------------------- |
| `ALLOW`            | Action may proceed. The firewall will independently re-check.         |
| `DENY`             | Action blocked. Logged to audit.                                       |
| `REQUIRE_HITL`     | Human must approve via `ApprovalEngine` before action executes.        |
| `DRY_RUN`          | Action may execute in dry-run mode only. External non-dry-run actions auto-upgrade to REQUIRE_HITL. |
| `UNKNOWN`          | Sentinel; treated as DENY by the engine.                              |

### 3.3 DEFAULT-DENY in the policy engine

If no rule matches, the engine returns the `default_decision` constructor
argument, which defaults to `PolicyDecision.DENY`.

---

## 4. Autonomy Levels (L0..L5)

**Location**: `albra_seed/autonomy/levels.py`, `albra_seed/autonomy/manager.py`

```python
class AutonomyLevel(int, Enum):
    L0_OBSERVE             = 0   # read-only observation
    L1_RECOMMEND           = 1   # may produce recommendations
    L2_EXECUTE_SAFE        = 2   # may execute LOW/MEDIUM actions autonomously
    L3_EXECUTE_EXTERNAL    = 3   # may execute external (provider) actions
    L4_EXECUTE_SENSITIVE   = 4   # may execute HIGH/CRITICAL actions
    L5_SELF_IMPROVE        = 5   # may modify its own DNA (gated by HITL)
```

### 4.1 Autonomy ≠ Permission — CRITICAL invariant

> **Autonomy describes what the agent is ALLOWED to decide on its own.**
> **Permission describes what the agent is AUTHORIZED to do.**
>
> These are independent gates. Both must be satisfied for an ALLOW.

| Scenario                                        | Autonomy | Permission | Outcome           |
| ----------------------------------------------- | -------- | ---------- | ----------------- |
| High autonomy, no permission                    | L5       | missing    | DENY              |
| Low autonomy, permission granted                 | L0       | granted    | REQUIRE_HITL      |
| Sufficient autonomy AND permission               | L2+      | granted    | ALLOW             |

### 4.2 AutonomyMatrix

The matrix maps `(action_type, risk_level) → required AutonomyLevel`. The seed
ships a DEFAULT-DENY matrix:

| action_type          | risk         | required level         |
| -------------------- | ------------ | ---------------------- |
| `observe`            | any          | L0_OBSERVE             |
| `recommend`          | any          | L1_RECOMMEND           |
| `execute_safe`       | LOW/MEDIUM   | L2_EXECUTE_SAFE        |
| `execute_safe`       | HIGH/CRIT    | L3_EXECUTE_EXTERNAL    |
| `execute_external`   | LOW/MEDIUM   | L3_EXECUTE_EXTERNAL    |
| `execute_external`   | HIGH/CRIT    | L4_EXECUTE_SENSITIVE   |
| `execute_sensitive`  | any          | L4_EXECUTE_SENSITIVE   |
| `self_improve`       | any          | L5_SELF_IMPROVE        |
| **unknown**          | any          | **L5_SELF_IMPROVE (DEFAULT-DENY)** |

Specialization layers MAY override entries via `AutonomyMatrix(overrides=...)`
but cannot lower the DEFAULT-DENY floor.

### 4.3 AutonomyProfile

Per-agent profile (`albra_seed/autonomy/profile.py`):
- `max_autonomous_risk`: highest RiskLevel the agent may execute autonomously.
- `allowed_action_types`: action_types this agent may attempt at all.
- `forbidden_action_types`: action_types this agent MUST NOT attempt regardless of autonomy.

---

## 5. HITL Approval Engine

**Location**: `albra_seed/approvals/engine.py`

The ApprovalEngine handles Human-In-The-Loop gating when the policy returns
`REQUIRE_HITL`.

### 5.1 Lifecycle

```
                  request(action, context, deadline)
                              │
                              ▼
                          PENDING
                       │     │     │
              approve  │     │     │ escalate
                      ▼     │     ▼
                APPROVED    │   ESCALATED  ── spawns child request (new ID)
                 [terminal] │
                           ▼
              reject        EXPIRED (deadline reached / cap exceeded)
                            [terminal]
```

### 5.2 Methods

| Method                          | Purpose                                                              |
| ------------------------------- | ------------------------------------------------------------------- |
| `request(action, ctx, deadline)` | Create a PENDING request. Returns `ApprovalResult(approval_id, PENDING)`. |
| `approve(id, approver)`         | Move PENDING → APPROVED. Records evidence.                            |
| `reject(id, approver, reason)`  | Move PENDING → REJECTED with reason. Records evidence.                |
| `expire(id)`                    | Move PENDING → EXPIRED.                                              |
| `escalate(id)`                  | Move PENDING → ESCALATED + spawn child with `escalation_of=parent`.  |
| `sweep_stale()`                 | Background sweep: expire/escalate stale PENDING requests.            |
| `verify_evidence_chain()`       | Cryptographic check that no approval record has been tampered.        |

### 5.3 Invariants

- **NEVER auto-approves**. The default action for stale requests is to
  ESCALATE (per the `EscalationPolicy`), not approve.
- Expired / approved / rejected requests are terminal; further transitions raise.
- Every transition is recorded as a `ApprovalEvidence` record, hash-chained
  via `ApprovalEvidenceStore.verify_chain()`.
- Escalation chain has a `max_escalations` cap; exceeding it auto-expires
  the request.

### 5.4 EscalationPolicy

```python
EscalationPolicy(
    stale_after_seconds=300,   # PENDING requests older than this are stale
    escalate_to="senior_approver",  # metadata only; routing is the caller's job
    max_escalations=3,         # auto-expire after this many escalation hops
    on_stale="escalate",       # "escalate" | "expire" | "reject"
)
```

---

## 6. Kill Switch

**Location**: `albra_seed/firewall/kill_switch.py`

The KillSwitch is the seed's single most powerful safety primitive. When
ACTIVE, EVERY external action is blocked (overrides explicit ALLOW rules and
even superuser contexts).

### 6.1 Properties

- **GLOBAL** — not per-agent, not per-action, not per-domain.
- **Audit-logged** — every activation/deactivation records `reason`, `actor`,
  `timestamp`, `activation_count` via an async audit sink.
- **Cooldown enforced** — after deactivation, re-activation is forbidden
  for `cooldown_after_deactivate_seconds` (default 30s) to prevent flapping.
- **Idempotent** — activating an already-active switch is a no-op (logged
  as a warning, not an error).

### 6.2 Activation

```python
await kill_switch.activate(
    reason="anomalous volume detected from external provider",
    activated_by="security-monitor",
)
# Every ActionFirewall.evaluate() now returns DENY with
# verdict.kill_switch_active=True.
```

### 6.3 Deactivation

```python
await kill_switch.deactivate(by="ops-oncall")
# kill_switch.in_cooldown() returns True for the next 30 seconds.
# Attempts to re-activate during cooldown should be refused by the caller.
```

---

## 7. Secrets Management

**Location**: `albra_seed/security/secrets.py`

Three interchangeable backends, all sharing the `SecretsManager` ABC:

| Backend                | Source                          | Use case                                              |
| ---------------------- | ------------------------------- | ----------------------------------------------------- |
| `EnvSecretsManager`   | `os.environ`                    | Default for CORE_ONLY mode. Read-write in-process.    |
| `FileSecretsManager`   | JSON or YAML file               | Local dev / single-host deployment. YAML lazy.        |
| `VaultSecretsManager`  | HashiCorp Vault via `hvac`      | Production. Lazy-imported; raises if `hvac` missing.  |

### 7.1 Never logged

The `_safe_log` helper in `secrets.py` STRIPS any kwarg whose name is in
`{value, secret, token, password, credential}` and replaces it with
`<redacted>`. Key names are safe to log; values never are.

Additionally, `config/secrets.py` ships:
- `SecretsAdapter` — returns `{name: is_set}` pairs, NEVER the values.
- `SecretsRedactor` — two-layer matcher (exact-key allowlist +
  word-boundary regex) applied to every CLI/API/config payload.
- `redact_secret_values()` — defence-in-depth function applied to every
  response emitted by the API routes and CLI formatter.

### 7.2 Vault backend

```python
from albra_seed.security.secrets import VaultSecretsManager

mgr = VaultSecretsManager(
    url="https://vault.albra.internal:8200",
    token=os.environ["VAULT_TOKEN"],   # NEVER logged
    mount_path="albra",
)
await mgr.set("openai_api_key", "sk-...")     # only logs: "secrets.set backend=vault key=openai_api_key"
value = await mgr.get("openai_api_key")      # caller responsible for not logging `value`
```

---

## 8. Tamper-Evident Audit

**Location**: `albra_seed/audit/tamper_evident.py`

The seed ships TWO separate audit logs — they are NEVER conflated:

| Log                       | Class                    | Tamper-evidence | Use case                                |
| ------------------------- | ------------------------ | --------------- | --------------------------------------- |
| **Normal audit**          | `AuditLog`                | No              | High-volume dashboards, SIEM ingestion. |
| **Tamper-evident audit**  | `TamperEvidentAuditLog`   | Yes (hash chain) | Compliance, chain-of-custody, kill-switch events, approval decisions. |

### 8.1 Hash chain

Each `TamperEvidentEntry` carries:
- `prev_hash` — SHA-256 of the previous entry's `record_hash` (genesis is
  `SHA-256("")`).
- `record_hash` — `SHA-256(prev_hash + "|" + canonical_json(content))` where
  `content` excludes the hash fields themselves.

Tampering with ANY field of ANY entry invalidates:
1. That entry's `record_hash` (content changed).
2. Every subsequent entry's `prev_hash` (chain broken).

### 8.2 Verification

```python
from albra_seed.audit.tamper_evident import TamperEvidentAuditLog

log = TamperEvidentAuditLog()
for i in range(5):
    log.append({"action": f"step-{i}", "actor": "agent-1"})

result = log.verify_chain()
assert result.valid                      # chain intact
assert result.chain_length == 5

# Tamper: mutate a historical entry.
log.all()[2].action = "TAMPERED"
result = log.verify_chain()
assert not result.valid                   # detected
assert result.broken_at == 2
assert result.kind in {"prev_hash_mismatch", "record_hash_mismatch"}
```

### 8.3 Separation from normal audit

The two logs are SEPARATE classes (not a flag on one class) to make it
impossible to accidentally include `prev_hash` / `record_hash` on a normal
audit entry, or to forget them on a tamper-evident entry.

---

## 9. Evidence Discipline

**Location**: `albra_seed/evidence/contract.py`

Every memory record / knowledge claim is backed by an `Evidence` object whose
`evidence_type` is one of:

| Type        | Meaning                                            | Promotion rules                                   |
| ----------- | -------------------------------------------------- | ------------------------------------------------- |
| `FACT`      | Verified by an external / tool / verified_provider source. | Requires `confidence ≥ 0.9` AND `source_type ∈ {tool, external, verified_provider}`. |
| `INFERENCE` | Produced by reasoning. May be promoted to FACT *if* it meets FACT requirements AND the caller explicitly invokes `promote_to_fact`. | NEVER auto-promoted. |
| `ASSUMPTION`| Stated without evidence.                            | NEVER promoted to FACT.                            |
| `UNKNOWN`   | Evidence type not yet determined.                   | **NEVER auto-promoted to FACT** (hard rule).      |

### 9.1 Hard rules (enforced in code + tests)

```python
from albra_seed.evidence.contract import EvidenceContract, EvidenceValidationError
from albra_seed.core.types import Evidence, EvidenceType

contract = EvidenceContract()

# 1. FACT requires confidence >= 0.9.
contract.validate(Evidence(
    source="probe", source_type="tool",
    evidence_type=EvidenceType.FACT, confidence=0.7,   # too low
))   # raises EvidenceValidationError

# 2. UNKNOWN never auto-promotes.
ev = Evidence(source="agent", source_type="agent",
              evidence_type=EvidenceType.UNKNOWN, confidence=0.99)
contract.promote_to_fact(ev)   # raises EvidenceValidationError
```

### 9.2 Evidence integrity

`EvidenceIntegrity` (`evidence/integrity.py`) computes SHA-256 over the
canonical JSON of an Evidence payload and compares using constant-time
comparison to defend against timing attacks.

### 9.3 Citation gate

`CitationGate` (`evidence/citation.py`) is the runtime hook used by the agent
loop's `RECORD` phase:
- `require_evidence(action)` — refuse to record a memory unless evidence is provided.
- `cite(record_id, evidence_ids)` — attach evidence to an existing record.
- `refuse(reason)` — log a refusal event to audit; memory write is blocked.
- `mark_unverified(record_id)` — flag a record whose evidence was later retracted.

---

## 10. Recovery Safety

**Location**: `albra_seed/recovery/manager.py`

The RecoveryManager classifies errors and dispatches strategies:

```
error → ErrorClassifier.classify() → ErrorClass
                                            │
        ┌──────────────────────────────────┼──────────────────────────────────┐
        ▼                                  ▼                                  ▼
    transient                       resource / external                    SECURITY / UNKNOWN
    (retry / fallback)              (retry + circuit breaker)             (REFUSE to auto-recover)
```

### 10.1 Hard rule

> **The seed NEVER auto-recovers SECURITY or UNKNOWN errors.** They are
> re-raised to the caller and recorded in BOTH audit logs. A human must
> triage them.

```python
from albra_seed.recovery.classifier import ErrorClass
# ErrorClass ∈ {transient, persistent, security, resource, external, unknown}
```

### 10.2 Dangerous marker refusal

The RecoveryManager REFUSES to register any strategy whose name matches
dangerous markers:

```
rm_rf, delete_database, drop_table, truncate, wipe,
kill_process, shutdown, format, factory_reset, ...
```

This prevents a misconfigured specialization layer from registering a
"recovery" strategy that actually destroys data.

### 10.3 Audit

Every recovery action — successful or not — is recorded in BOTH:
- The normal audit log (for operator visibility).
- The tamper-evident audit log (for compliance).

---

## 11. Default-Deny Summary

| Component                          | Default action when no rule / unknown input                    |
| ---------------------------------- | ------------------------------------------------------------- |
| ActionFirewall                     | DENY (no matching rule)                                       |
| PolicyEngine                       | DENY (no matching rule)                                        |
| AutonomyMatrix                     | L5 required (unknown action_type / risk_level)                |
| PermissionRegistry                 | No permission granted (DEFAULT-DENY)                          |
| Audit ACL                          | Deny (no matching ACL entry)                                  |
| EvidenceContract                   | Raise EvidenceValidationError                                 |
| RecoveryManager (SECURITY/UNKNOWN) | Refuse to auto-recover; re-raise to caller                    |
| KillSwitch                         | When active: block ALL actions                                |

---

## 12. Invariant Checklist

These invariants are enforced by code AND covered by tests:

- [x] DEFAULT-DENY in firewall, policy, permissions, autonomy, audit ACL.
- [x] Autonomy ≠ Permission (both gates checked independently).
- [x] UNKNOWN evidence never auto-promotes to FACT.
- [x] FACT requires `confidence ≥ 0.9` AND qualifying `source_type`.
- [x] Tamper-evident audit separate from normal audit.
- [x] Hash chain detects modification of any historical entry.
- [x] Kill switch is global and overrides explicit ALLOW rules.
- [x] Kill switch deactivation enforces a cooldown period.
- [x] Approval engine NEVER auto-approves (escalate is the default stale action).
- [x] Secrets never appear in logs (redacted at the `_safe_log` boundary).
- [x] RecoveryManager refuses SECURITY/UNKNOWN auto-recovery.
- [x] RecoveryManager refuses to register strategies matching dangerous markers.

---

## 13. Specialization Boundary

A specialization layer MUST NOT:

- Bypass the ActionFirewall (use `FirewallRule` to allow actions).
- Disable the PolicyEngine (extend `PolicyRuleSet` instead).
- Lower the AutonomyMatrix DEFAULT-DENY floor (L5).
- Disable the KillSwitch (it has no disable API).
- Replace the AuditLog with a non-append-only implementation.
- Replace the TamperEvidentAuditLog (it has no swap API).
- Auto-promote UNKNOWN evidence to FACT (the contract refuses).
- Auto-approve stale HITL requests (the engine refuses).
- Log secret values (use `SecretsManager.get()` and handle the value carefully).

See [`SPECIALIZATION.md`](SPECIALIZATION.md) for the full contract.
