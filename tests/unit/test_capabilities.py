"""Unit tests for the capabilities subpackage."""

from albra_seed.capabilities import (
    CapabilityMatrix,
    CapabilityRegistry,
    CapabilityResolver,
)
from albra_seed.core.base import Capability
from albra_seed.core.types import RiskLevel


def _make_capability(
    name: str = "respond",
    *,
    tools: list[str] | None = None,
    providers: list[str] | None = None,
    permissions: list[str] | None = None,
    risk: RiskLevel = RiskLevel.LOW,
) -> Capability:
    return Capability(
        name=name,
        description=f"capability {name}",
        requires_tools=tools or [],
        requires_providers=providers or [],
        requires_permissions=permissions or [],
        risk_level=risk,
    )


# -------------------------------------------------------------------
# 1. Registry register + get
# -------------------------------------------------------------------


def test_capability_registry_register_and_get():
    reg = CapabilityRegistry()
    cap = _make_capability("respond", tools=["echo"], providers=["mock"])
    reg.register(cap)
    assert "respond" in reg.list()
    out = reg.get("respond")
    assert out.name == "respond"
    assert out.requires_tools == ["echo"]
    assert reg.is_enabled("respond") is True


# -------------------------------------------------------------------
# 2. Resolver detects missing tools
# -------------------------------------------------------------------


def test_capability_resolver_detects_missing_tools():
    reg = CapabilityRegistry()
    reg.register(_make_capability("respond", tools=["echo", "search"], providers=["mock"]))
    resolver = CapabilityResolver(
        reg,
        available_tools={"echo"},
        available_providers={"mock"},
    )
    res = resolver.resolve("respond")
    assert res.satisfied is False
    assert "search" in res.missing_tools
    assert res.missing_providers == []
    assert any("search" in r for r in res.reasons)


# -------------------------------------------------------------------
# 3. Resolver detects missing providers and permissions
# -------------------------------------------------------------------


def test_capability_resolver_detects_missing_providers_and_permissions():
    reg = CapabilityRegistry()
    reg.register(
        _make_capability(
            "external_lookup",
            tools=["search"],
            providers=["openai"],
            permissions=["data.read"],
            risk=RiskLevel.MEDIUM,
        )
    )
    resolver = CapabilityResolver(
        reg,
        available_tools={"search"},
        available_providers=set(),
        available_permissions=set(),
    )
    res = resolver.resolve("external_lookup")
    assert res.satisfied is False
    assert "openai" in res.missing_providers
    assert "data.read" in res.missing_permissions


# -------------------------------------------------------------------
# 4. CapabilityMatrix maps to requirements
# -------------------------------------------------------------------


def test_capability_matrix_maps_to_requirements():
    reg = CapabilityRegistry()
    reg.register(_make_capability("respond", tools=["echo"], permissions=["basic.use"], risk=RiskLevel.LOW))
    reg.register(_make_capability("external_lookup", tools=["search"], providers=["openai"], risk=RiskLevel.HIGH))
    matrix = CapabilityMatrix(reg)

    assert matrix.requires_tools("respond") == ["echo"]
    assert matrix.requires_providers("respond") == []
    assert matrix.requires_permissions("respond") == ["basic.use"]
    assert matrix.risk_level("respond") == RiskLevel.LOW

    assert matrix.requires_tools("external_lookup") == ["search"]
    assert matrix.requires_providers("external_lookup") == ["openai"]
    assert matrix.risk_level("external_lookup") == RiskLevel.HIGH
    assert matrix.is_external("external_lookup") is False

    # Add manual entry
    matrix.add("manual_cap", requires_tools=["x"], risk_level=RiskLevel.CRITICAL)
    assert matrix.risk_level("manual_cap") == RiskLevel.CRITICAL

    # Unknown capability returns UNKNOWN risk
    assert matrix.risk_level("does_not_exist") == RiskLevel.UNKNOWN


# -------------------------------------------------------------------
# 5. Resolver handles unregistered capabilities
# -------------------------------------------------------------------


def test_capability_resolver_handles_unknown_capability():
    reg = CapabilityRegistry()
    resolver = CapabilityResolver(reg)
    res = resolver.resolve("nonexistent")
    assert res.satisfied is False
    assert any("not registered" in r for r in res.reasons)


# -------------------------------------------------------------------
# 6. Disabled capability cannot be resolved as satisfied
# -------------------------------------------------------------------


def test_capability_resolver_disabled_capability_unsatisfied():
    reg = CapabilityRegistry()
    reg.register(_make_capability("respond", tools=["echo"]))
    reg.disable("respond")
    resolver = CapabilityResolver(reg, available_tools={"echo"})
    res = resolver.resolve("respond")
    assert res.satisfied is False
    assert any("disabled" in r for r in res.reasons)
