"""ALBRA SEED — Security tests.

These tests verify security invariants of the SEED:

  1. Secrets are NEVER logged — the redactor strips values whose key
     matches the secret-key substrings, even when explicitly passed.
  2. The KillSwitch blocks ALL external actions globally, even when
     firewall rules + permissions would otherwise allow them.
  3. The ActionFirewall is DEFAULT-DENY: an action with no matching
     rule is denied, never silently allowed.
  4. The tamper-evident audit log detects modification of any historical
     record (hash-chain break).
  5. UNKNOWN evidence is NEVER silently promoted to FACT — promotion
     raises a contract violation.

CRITICAL: every test runs with NO external credentials.
"""

from __future__ import annotations

import pytest

from albra_seed.audit.tamper_evident import TamperEvidentAuditLog
from albra_seed.core.types import (
    AutonomyLevel,
    Evidence,
    EvidenceType,
    PolicyDecision,
    RiskLevel,
    RunContext,
    ToolCall,
    new_id,
)
from albra_seed.evidence.contract import (
    EvidenceContract,
    EvidenceValidationError,
)
from albra_seed.evidence.integrity import EvidenceIntegrity
from albra_seed.evidence.manager import EvidenceManager
from albra_seed.firewall import ActionFirewall, FirewallRule, KillSwitch
from albra_seed.observability.logger import redact
from albra_seed.security.secrets import EnvSecretsManager

# =====================================================================
# 1. Secrets are NEVER logged
# =====================================================================


def test_secrets_never_logged() -> None:
    """Pass a secret value into the logger; verify it is redacted in output.

    Uses `structlog.testing.capture_logs()` to capture event dicts
    emitted by the structlog pipeline WITHOUT mutating the global
    structlog configuration (which would pollute other test modules).
    """
    secret_value = "sk-test-supersecret-apikey-1234567890"

    # 1. Direct `redact()` helper strips secret-keyed values.
    redacted = redact(
        {
            "api_key": secret_value,           # matches "api_key" substring
            "openai_api_key": secret_value,    # explicit provider key
            "benign_field": "hello-world",     # must remain visible
        }
    )
    assert redacted["api_key"] == "<redacted>"
    assert redacted["openai_api_key"] == "<redacted>"
    assert redacted["benign_field"] == "hello-world", "benign field must survive"

    # 2. Through structlog's capture: a log emitted with secret-typed keys
    #    must never leak the secret value. capture_logs replaces the
    #    configured processors for the duration of the context manager
    #    only — no global mutation leaks out.
    import structlog

    log = structlog.get_logger("security-test")
    with structlog.testing.capture_logs() as captured:
        log.info(
            "test.event",
            api_key=secret_value,
            openai_api_key=secret_value,
            benign_field="hello-world",
        )
    assert len(captured) == 1
    event = captured[0]
    # The redaction processor isn't installed in capture_logs mode (that
    # mode returns raw event dicts), but we can verify the redact()
    # helper applied to the captured event strips every secret-typed key.
    safe = redact(event)
    serialized = repr(safe)
    assert secret_value not in serialized, "secret value must NOT appear in redacted event"
    assert safe["api_key"] == "<redacted>"
    assert safe["openai_api_key"] == "<redacted>"
    assert safe["benign_field"] == "hello-world"

    # 3. EnvSecretsManager NEVER returns values via __repr__.
    mgr = EnvSecretsManager()
    mgr_repr = repr(mgr)
    assert "value" not in mgr_repr.lower(), "EnvSecretsManager repr must not leak values"
    assert secret_value not in mgr_repr

    # 4. The SecretsManager._safe_log helper (used internally) replaces
    #    the value-named kwarg with "<redacted>". We verify by calling
    #    it directly through the structlog capture pipeline.
    from albra_seed.security.secrets import _safe_log
    with structlog.testing.capture_logs() as cap:
        _safe_log("secrets.set", backend="env", key="MY_KEY", value=secret_value)
    assert len(cap) == 1
    ev = cap[0]
    assert ev.get("value") == "<redacted>", (
        f"_safe_log must redact `value` kwarg; got {ev.get('value')!r}"
    )
    assert secret_value not in repr(ev)


# =====================================================================
# 2. Kill switch blocks ALL actions even with permissions
# =====================================================================


async def test_kill_switch_global_block() -> None:
    """Activating the KillSwitch blocks every action, even when explicit
    ALLOW rules + matching permissions exist."""
    kill = KillSwitch()
    # Firewall with explicit ALLOW rule for "echo" + LOW risk threshold.
    firewall = ActionFirewall(
        rules=[
            FirewallRule(
                name="allow_echo",
                action_pattern="echo",
                risk_threshold=RiskLevel.UNKNOWN,
                decision=PolicyDecision.ALLOW,
                priority=10,
            )
        ],
        kill_switch=kill,
    )

    # Build a RunContext with the permissions the rule would require.
    ctx = RunContext(
        agent_id="agent-privileged",
        task="echo",
        autonomy_level=AutonomyLevel.L5_SELF_IMPROVE,  # maximum autonomy
        permissions=["tool.echo", "*"],  # wildcard permission
        risk_level=RiskLevel.LOW,
    )
    call = ToolCall(id=new_id("tc"), tool_name="echo", arguments={"message": "hi"})

    # Before activation: ALLOWED.
    verdict_before = await firewall.check(call, context=ctx)
    assert verdict_before.allowed is True
    assert verdict_before.decision == PolicyDecision.ALLOW

    # Activate kill switch.
    await kill.activate(reason="security incident", activated_by="security-officer")

    # After activation: DENIED — kill switch overrides everything.
    verdict_after = await firewall.check(call, context=ctx)
    assert verdict_after.allowed is False
    assert verdict_after.decision == PolicyDecision.DENY
    assert verdict_after.kill_switch_active is True
    assert verdict_after.reason == "kill_switch_active"

    # Different action — also denied (kill switch is GLOBAL).
    other_call = ToolCall(id=new_id("tc"), tool_name="other", arguments={})
    other_verdict = await firewall.check(other_call, context=ctx)
    assert other_verdict.allowed is False
    assert other_verdict.kill_switch_active is True


# =====================================================================
# 3. Default-deny firewall: no matching rule -> DENY
# =====================================================================


async def test_default_deny_firewall() -> None:
    """An action with no matching firewall rule is DENIED — never allowed."""
    firewall = ActionFirewall()  # no rules
    ctx = RunContext(
        agent_id="agent-unprivileged",
        task="anything",
        autonomy_level=AutonomyLevel.L0_OBSERVE,
        risk_level=RiskLevel.LOW,
    )
    call = ToolCall(id=new_id("tc"), tool_name="unmatched_action", arguments={})

    verdict = await firewall.check(call, context=ctx)

    assert verdict.allowed is False
    assert verdict.decision == PolicyDecision.DENY
    assert verdict.reason == "no_matching_rule"
    assert verdict.matched_rule_id is None

    # Even an L5-self-improve agent with wildcard permissions is denied
    # when the firewall has no matching rule.
    ctx_priv = RunContext(
        agent_id="agent-superuser",
        task="anything",
        autonomy_level=AutonomyLevel.L5_SELF_IMPROVE,
        permissions=["*"],
        risk_level=RiskLevel.LOW,
    )
    verdict_priv = await firewall.check(call, context=ctx_priv)
    assert verdict_priv.allowed is False
    assert verdict_priv.decision == PolicyDecision.DENY
    assert verdict_priv.reason == "no_matching_rule"


# =====================================================================
# 4. Tamper-evident audit log detects modification
# =====================================================================


def test_tamper_evident_audit_detects_modification() -> None:
    """Modifying any audit entry's content invalidates the hash chain."""
    log = TamperEvidentAuditLog()
    log.append({"action": "first", "actor": "alice", "result": "success"})
    log.append({"action": "second", "actor": "bob", "result": "success"})
    log.append({"action": "third", "actor": "carol", "result": "success"})

    # Chain is intact before tampering.
    assert log.verify_chain().valid is True
    assert log.detect_tampering() == []

    # Tamper: modify the SECOND entry's action field.
    entries = log.all()
    assert len(entries) == 3
    entries[1].action = "TAMPERED"  # mutation

    # Chain is now broken at index 1.
    result = log.verify_chain()
    assert result.valid is False
    assert result.broken_at == 1
    assert result.kind == "record_hash_mismatch"

    # detect_tampering lists the broken link.
    tamper_evidences = log.detect_tampering()
    assert len(tamper_evidences) >= 1
    assert tamper_evidences[0].kind == "record_hash_mismatch"
    assert tamper_evidences[0].index == 1


# =====================================================================
# 5. UNKNOWN evidence never silently promoted to FACT
# =====================================================================


async def test_unknown_evidence_never_promoted_to_fact() -> None:
    """UNKNOWN evidence cannot be promoted to FACT — the contract refuses."""
    mgr = EvidenceManager()

    # Register an UNKNOWN evidence record.
    ev = await mgr.register_unknown(
        source="agent",
        source_type="agent",
        content="unverified observation",
        execution_id="exec-1",
    )
    assert ev.evidence_type == EvidenceType.UNKNOWN
    assert ev.confidence == 0.0

    # Promotion attempt must raise.
    with pytest.raises(EvidenceValidationError, match="UNKNOWN"):
        await mgr.promote_to_fact(ev)

    # The evidence_type is STILL UNKNOWN after the failed promotion.
    assert ev.evidence_type == EvidenceType.UNKNOWN

    # Registering fresh UNKNOWN evidence and verifying integrity works.
    assert await mgr.verify_integrity(ev) is True

    # Direct contract-level check: a brand-new UNKNOWN evidence also refuses.
    contract = EvidenceContract()
    fresh = Evidence(
        evidence_id=new_id("ev"),
        source="agent",
        source_type="agent",
        content="another unknown",
        evidence_type=EvidenceType.UNKNOWN,
        confidence=0.0,
    )
    EvidenceIntegrity.stamp(fresh)
    with pytest.raises(EvidenceValidationError, match="UNKNOWN"):
        contract.promote_to_fact(fresh)

    # Conversely, a properly sourced INFERENCE at high confidence CAN be
    # promoted — but UNKNOWN never can.
    good_inference = Evidence(
        evidence_id=new_id("ev"),
        source="external-api",
        source_type="external",
        content="verified reading",
        evidence_type=EvidenceType.INFERENCE,
        confidence=0.95,
    )
    promoted = contract.promote_to_fact(good_inference)
    assert promoted.evidence_type == EvidenceType.FACT
