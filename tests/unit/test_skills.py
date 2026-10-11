"""Unit tests for the skills subpackage."""

import asyncio
import pytest

from albra_seed.core.types import RiskLevel
from albra_seed.skills import (
    SkillContract,
    SkillLifecycle,
    SkillRegistry,
    SkillValidator,
)


def _make_contract(
    name: str = "noop_skill",
    *,
    permissions: list[str] | None = None,
    risk: RiskLevel = RiskLevel.LOW,
    dependencies: list[str] | None = None,
) -> SkillContract:
    return SkillContract(
        name=name,
        version="1.0.0",
        objective="A test skill that does nothing meaningful.",
        inputs={"description": "any input"},
        outputs={"description": "any output"},
        input_schema={"type": "object", "additionalProperties": True},
        output_schema={"type": "object"},
        permissions=permissions or [],
        dependencies=dependencies or [],
        risk_level=risk,
        evidence_required=False,
        tests=["test_noop_skill_basic"],
        acceptance_criteria=["returns success"],
        max_duration_seconds=5,
    )


async def _noop_handler(args, context):
    return {"ok": True, "received": args}


# -------------------------------------------------------------------
# 1. Registry register + execute
# -------------------------------------------------------------------


def test_skill_registry_register_and_execute():
    reg = SkillRegistry()
    contract = _make_contract("noop_skill")
    vres = reg.register(contract, _noop_handler)
    assert vres.valid
    assert "noop_skill" in reg.list()
    assert reg.is_enabled("noop_skill") is True

    result = asyncio.run(reg.execute("noop_skill", {"x": 1}))
    assert result.success
    assert result.output["ok"] is True
    assert result.output["received"] == {"x": 1}


# -------------------------------------------------------------------
# 2. Validator rejects invalid contract
# -------------------------------------------------------------------


def test_skill_validator_rejects_invalid_contract():
    validator = SkillValidator(known_tool_ids={"echo"})

    # missing name
    bad = SkillContract(
        name="",
        objective="do something",
        risk_level=RiskLevel.LOW,
        inputs={"description": "x"},
    )
    res = validator.validate(bad)
    assert not res.valid
    assert any("name" in e for e in res.errors)

    # high-risk skill with no permissions
    high_risk = SkillContract(
        name="risky",
        objective="risky op",
        risk_level=RiskLevel.HIGH,
        inputs={"description": "x"},
        permissions=[],
    )
    res = validator.validate(high_risk)
    assert not res.valid
    assert any("permissions" in e for e in res.errors)

    # unknown dependency tool
    unknown_dep = SkillContract(
        name="dep",
        objective="needs a tool",
        risk_level=RiskLevel.LOW,
        inputs={"description": "x"},
        dependencies=["nonexistent_tool"],
    )
    res = validator.validate(unknown_dep)
    assert not res.valid
    assert any("unknown tool dependencies" in e for e in res.errors)


# -------------------------------------------------------------------
# 3. Lifecycle tracks activation
# -------------------------------------------------------------------


def test_skill_lifecycle_tracks_activation():
    lc = SkillLifecycle()
    state = lc.install("skill_a", version="1.0.0")
    assert state.value == "pending"
    assert lc.state("skill_a").value == "pending"

    state = lc.activate("skill_a")
    assert state.value == "active"
    assert lc.is_active("skill_a") is True

    state = lc.deactivate("skill_a")
    assert state.value == "inactive"
    assert lc.is_active("skill_a") is False

    state = lc.activate("skill_a")
    assert state.value == "active"

    history = lc.history("skill_a")
    assert len(history) >= 4
    assert history[0].to_state.value == "pending"
    assert history[1].to_state.value == "active"


# -------------------------------------------------------------------
# 4. Skill can be disabled
# -------------------------------------------------------------------


def test_skill_can_be_disabled():
    reg = SkillRegistry()
    contract = _make_contract("disable_me")
    reg.register(contract, _noop_handler)
    assert reg.is_enabled("disable_me") is True

    reg.disable("disable_me")
    assert reg.is_enabled("disable_me") is False

    # Execution of disabled skill fails cleanly (not via exception path)
    result = asyncio.run(reg.execute("disable_me", {}))
    assert not result.success
    assert "not active" in (result.error or "")

    reg.enable("disable_me")
    assert reg.is_enabled("disable_me") is True
    result = asyncio.run(reg.execute("disable_me", {}))
    assert result.success


# -------------------------------------------------------------------
# 5. SkillExecutor enforces permissions
# -------------------------------------------------------------------


def test_skill_executor_enforces_permissions():
    reg = SkillRegistry()
    contract = _make_contract("perm_protected", permissions=["data.write"], risk=RiskLevel.HIGH)
    reg.register(contract, _noop_handler)

    # caller without permission -> denied
    result = asyncio.run(reg.execute("perm_protected", {}, caller_permissions=set()))
    assert not result.success
    assert "permission denied" in (result.error or "")
    assert result.output.get("missing_permissions") == ["data.write"]

    # caller with permission -> success
    result = asyncio.run(
        reg.execute("perm_protected", {}, caller_permissions={"data.write"})
    )
    assert result.success
    assert result.output["ok"] is True


# -------------------------------------------------------------------
# 6. Version upgrade works
# -------------------------------------------------------------------


def test_skill_version_upgrade():
    reg = SkillRegistry()
    contract = _make_contract("upgradable")
    reg.register(contract, _noop_handler)
    assert reg.get("upgradable").version == "1.0.0"

    reg.upgrade_version("upgradable", "2.0.0")
    assert reg.get("upgradable").version == "2.0.0"

    # lifecycle history reflects upgrade — look up by skill_id
    history = reg.lifecycle.history(contract.skill_id)
    assert any("version upgrade" in e.reason for e in history)
