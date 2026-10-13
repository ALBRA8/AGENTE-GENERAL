"""Unit tests for the tools subpackage."""

import asyncio
import pytest

from albra_seed.core.types import RunContext
from albra_seed.tools import (
    ContractValidator,
    DryRunExecutor,
    EchoTool,
    HealthCheckTool,
    NoOpTool,
    ToolExecutor,
    ToolRegistry,
    ToolTimeoutError,
)


# -------------------------------------------------------------------
# 1. Registry register + get
# -------------------------------------------------------------------


def test_tool_registry_register_and_get():
    reg = ToolRegistry()
    reg.register(EchoTool())
    assert "echo" in reg.list()
    tool = reg.get("echo")
    assert isinstance(tool, EchoTool)
    contract = reg.get_contract("echo")
    assert contract.name == "echo"
    assert reg.availability("echo") == "available"


# -------------------------------------------------------------------
# 2. EchoTool returns input
# -------------------------------------------------------------------


def test_echo_tool_returns_input():
    echo = EchoTool()
    result = asyncio.run(echo.execute({"message": "hello world"}))
    assert result.success
    assert result.output["echoed"] == "hello world"
    assert "received_at" in result.output


# -------------------------------------------------------------------
# 3. HealthCheckTool returns component status
# -------------------------------------------------------------------


def test_health_check_tool_returns_component_status():
    hc = HealthCheckTool()
    hc.register_component("memory_store", "healthy")
    hc.register_component("provider_router", "degraded")

    r1 = asyncio.run(hc.execute({"component": "memory_store"}))
    assert r1.success
    assert r1.output["component"] == "memory_store"
    assert r1.output["status"] == "healthy"

    r2 = asyncio.run(hc.execute({"component": "provider_router"}))
    assert r2.output["status"] == "degraded"

    r3 = asyncio.run(hc.execute({"component": "unknown"}))
    assert r3.output["status"] == "not_configured"


# -------------------------------------------------------------------
# 4. ToolExecutor applies timeout
# -------------------------------------------------------------------


def test_tool_executor_applies_timeout():
    reg = ToolRegistry()

    # Build a contract with a very short timeout and a slow tool
    from albra_seed.core.base import Tool, ToolContract
    from albra_seed.core.types import RiskLevel, ToolResult, new_id

    class SlowTool(Tool):
        def __init__(self) -> None:
            self.contract = ToolContract(
                name="slow",
                description="slow tool",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                risk_level=RiskLevel.LOW,
                timeout_seconds=1,
                is_dry_run_capable=False,
            )

        async def execute(self, arguments, *, context=None, dry_run=False):
            await asyncio.sleep(2)
            return ToolResult(call_id=new_id("tc"), success=True, output={"ok": True})

    reg.register(SlowTool())
    # H-02 amendment: ToolExecutor is now FAIL-CLOSED — it rejects
    # any non-dry-run execution unless an ``AuthorizationVerifier`` is
    # configured AND a valid ``AuthorizationToken`` is supplied. The
    # old ``_AllowVerdict(allowed=True)`` stub is no longer accepted.
    # We wire an issuer+verifier and issue a token bound to the same
    # (tool_name, principal_id, execution_id) the executor will verify
    # against — so the gate opens and the tool is actually invoked,
    # after which its 2-second sleep blows past the 1-second timeout.
    from albra_seed.security.auth import AuthPrincipal
    from albra_seed.security.trusted_principal import TrustedPrincipal, TrustedPrincipalIssuer, TrustedPrincipalVerifier
    from albra_seed.security.auth_token import (
        AuthorizationIssuer,
        AuthorizationVerifier,
    )

    issuer = AuthorizationIssuer()
    verifier = AuthorizationVerifier(issuer)
    tp_issuer = TrustedPrincipalIssuer()
    executor = ToolExecutor(
        reg,
        auth_verifier=verifier,
        trusted_principal_verifier=TrustedPrincipalVerifier(tp_issuer),
    )
    ctx = RunContext(agent_id="test", task="t", execution_id="exec-1", context_id="ctx-1")
    ctx.verified_principal = tp_issuer.seal(AuthPrincipal(
        identity="alice", is_anonymous=False
    ))
    token = issuer.issue(
        tool_name="slow",
        principal_id="alice",
        execution_id="exec-1",
        context_id='ctx-1',
    )
    result = asyncio.run(
        executor.execute(
            "slow", {}, context=ctx, authorization=token, call_id="call-1",
        )
    )
    assert not result.success
    assert "timeout" in (result.error or "")
    assert result.output.get("timed_out") is True


# -------------------------------------------------------------------
# 5. ContractValidator rejects invalid input
# -------------------------------------------------------------------


def test_contract_validator_rejects_invalid_input():
    validator = ContractValidator()
    schema = {
        "type": "object",
        "required": ["message"],
        "properties": {"message": {"type": "string"}},
        "additionalProperties": False,
    }

    ok = validator.validate_input({"message": "hi"}, schema)
    assert ok.valid

    bad = validator.validate_input({}, schema)
    assert not bad.valid
    assert any("message" in e for e in bad.errors)

    bad_type = validator.validate_input({"message": 42}, schema)
    assert not bad_type.valid


# -------------------------------------------------------------------
# 6. DryRunExecutor simulates
# -------------------------------------------------------------------


def test_dry_run_executor_simulates():
    reg = ToolRegistry()
    reg.register(EchoTool())
    reg.register(NoOpTool())
    dry = DryRunExecutor(reg)

    r_echo = asyncio.run(dry.simulate("echo", {"message": "test"}))
    assert r_echo.success
    assert r_echo.output.get("dry_run") is True
    assert r_echo.output["echoed"] == "test"

    r_noop = asyncio.run(dry.simulate("noop", {}))
    assert r_noop.success
    assert r_noop.output.get("dry_run") is True


# -------------------------------------------------------------------
# 7. DryRunExecutor returns synthesized result for non-dry-run tools
# -------------------------------------------------------------------


def test_dry_run_executor_synthesizes_for_non_capable_tool():
    from albra_seed.core.base import Tool, ToolContract
    from albra_seed.core.types import RiskLevel, ToolResult, new_id

    class NonDryRunTool(Tool):
        def __init__(self) -> None:
            self.contract = ToolContract(
                name="real_action",
                description="real action",
                input_schema={"type": "object"},
                risk_level=RiskLevel.HIGH,
                is_dry_run_capable=False,
            )

        async def execute(self, arguments, *, context=None, dry_run=False):
            # In real use this would have side effects; the DryRunExecutor
            # should never call it.
            raise AssertionError("dry-run executor must not call non-capable tool")

    reg = ToolRegistry()
    reg.register(NonDryRunTool())
    dry = DryRunExecutor(reg)
    result = asyncio.run(dry.simulate("real_action", {}))
    assert result.success
    assert result.output.get("skipped_execution") is True


# -------------------------------------------------------------------
# 8. Registry disables tool and get raises
# -------------------------------------------------------------------


def test_tool_registry_disabled_tool_unavailable():
    reg = ToolRegistry()
    reg.register(EchoTool())
    reg.disable("echo")
    assert reg.availability("echo").value == "disabled"
    with pytest.raises(Exception):
        reg.get("echo")
    # re-enable works
    reg.enable("echo")
    assert reg.get("echo") is not None
