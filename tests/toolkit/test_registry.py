"""Tests for the toolkit — uniform tool system.

Covers:
- Tool registration and discovery.
- Permission checks (read-only, side-effect, denied principal, approval).
- Execution with timeout.
- Error handling (unknown tool, missing callable, invalid args).
- Invocation logging (result, duration, no secrets).
- Built-in example tools (echo, calculator, timestamp).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from albra_seed.toolkit import (
    ToolCategory,
    ToolDefinition,
    ToolPermission,
    ToolRegistryV2,
)
from albra_seed.core.types import ToolResult, new_id


# =====================================================================
# Helpers
# =====================================================================


async def _ok_tool(args: dict[str, Any], *, context: Any = None) -> ToolResult:
    return ToolResult(call_id=new_id("tc"), success=True, output={"ok": True})


async def _fail_tool(args: dict[str, Any], *, context: Any = None) -> ToolResult:
    return ToolResult(call_id=new_id("tc"), success=False, error="intentional failure")


async def _slow_tool(args: dict[str, Any], *, context: Any = None) -> ToolResult:
    await asyncio.sleep(10)
    return ToolResult(call_id=new_id("tc"), success=True, output={"slow": True})


def _make_def(name: str = "test", **kw) -> ToolDefinition:
    return ToolDefinition(name=name, **kw)


# =====================================================================
# Tests
# =====================================================================


class TestRegistration:
    def test_register_and_get(self):
        reg = ToolRegistryV2()
        d = _make_def("echo")
        reg.register(d, _ok_tool)
        assert reg.get("echo") is d

    def test_duplicate_raises(self):
        reg = ToolRegistryV2()
        reg.register(_make_def("echo"), _ok_tool)
        with pytest.raises(ValueError, match="already registered"):
            reg.register(_make_def("echo"), _ok_tool)

    def test_unregister(self):
        reg = ToolRegistryV2()
        reg.register(_make_def("echo"), _ok_tool)
        assert reg.unregister("echo") is True
        assert reg.get("echo") is None
        assert reg.unregister("echo") is False

    def test_list_tools(self):
        reg = ToolRegistryV2()
        reg.register(_make_def("a"), _ok_tool)
        reg.register(_make_def("b"), _ok_tool)
        assert len(reg.list_tools()) == 2

    def test_list_by_category(self):
        reg = ToolRegistryV2()
        reg.register(
            _make_def("read", category=ToolCategory.READ_ONLY), _ok_tool
        )
        reg.register(
            _make_def("write", category=ToolCategory.SIDE_EFFECT), _ok_tool
        )
        assert len(reg.list_by_category(ToolCategory.READ_ONLY)) == 1
        assert len(reg.list_by_category(ToolCategory.SIDE_EFFECT)) == 1


class TestPermissions:
    def test_read_only_allowed(self):
        p = ToolPermission(category=ToolCategory.READ_ONLY)
        assert p.can_execute(principal_id="alice", autonomy_level=0) is True

    def test_denied_principal(self):
        p = ToolPermission(denied_principals=["attacker"])
        assert p.can_execute(principal_id="attacker") is False
        assert p.can_execute(principal_id="alice") is True

    def test_insufficient_autonomy(self):
        p = ToolPermission(allowed_autonomy_levels=[3, 4, 5])
        assert p.can_execute(autonomy_level=0) is False
        assert p.can_execute(autonomy_level=3) is True

    def test_requires_approval_without(self):
        p = ToolPermission(requires_approval=True)
        assert p.can_execute(has_approval=False) is False

    def test_requires_approval_with(self):
        p = ToolPermission(requires_approval=True)
        assert p.can_execute(has_approval=True) is True


class TestExecution:
    @pytest.mark.asyncio
    async def test_success(self):
        reg = ToolRegistryV2()
        reg.register(_make_def("ok"), _ok_tool)
        result = await reg.execute("ok", {})
        assert result.success
        assert result.output == {"ok": True}

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        reg = ToolRegistryV2()
        result = await reg.execute("missing", {})
        assert not result.success
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        reg = ToolRegistryV2()
        reg.register(
            _make_def(
                "restricted",
                permission=ToolPermission(
                    requires_approval=True,
                    allowed_autonomy_levels=[4, 5],
                ),
            ),
            _ok_tool,
        )
        result = await reg.execute("restricted", {}, autonomy_level=0)
        assert not result.success
        assert "permission denied" in result.error

    @pytest.mark.asyncio
    async def test_timeout(self):
        reg = ToolRegistryV2()
        reg.register(
            _make_def("slow", timeout_seconds=0.1),
            _slow_tool,
        )
        result = await reg.execute("slow", {})
        assert not result.success
        assert "timeout" in result.error

    @pytest.mark.asyncio
    async def test_error_handled(self):
        reg = ToolRegistryV2()
        reg.register(_make_def("fail"), _fail_tool)
        result = await reg.execute("fail", {})
        assert not result.success
        assert "intentional" in result.error


    @pytest.mark.asyncio
    async def test_input_schema_rejects_missing_required_field(self):
        reg = ToolRegistryV2()
        reg.register(
            _make_def(
                "validated",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            ),
            _ok_tool,
        )
        result = await reg.execute("validated", {})
        assert not result.success
        assert "invalid arguments" in result.error
        assert result.output == {"invalid_arguments": True}
        assert reg.invocations()[0].success is False

    @pytest.mark.asyncio
    async def test_input_schema_rejects_wrong_type(self):
        reg = ToolRegistryV2()
        reg.register(
            _make_def(
                "validated",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            ),
            _ok_tool,
        )
        result = await reg.execute("validated", {"value": 42})
        assert not result.success
        assert "property value" in result.error

    @pytest.mark.asyncio
    async def test_side_effect_requires_approval_even_with_default_permission(self):
        reg = ToolRegistryV2()
        called = False

        async def side_effect(args, *, context=None):
            nonlocal called
            called = True
            return ToolResult(call_id=new_id("tc"), success=True, output={"done": True})

        reg.register(
            _make_def("write", category=ToolCategory.SIDE_EFFECT),
            side_effect,
        )
        denied = await reg.execute("write", {})
        assert not denied.success
        assert not called
        allowed = await reg.execute("write", {}, has_approval=True)
        assert allowed.success
        assert called

    @pytest.mark.asyncio
    async def test_output_schema_failure_is_not_reported_as_success(self):
        reg = ToolRegistryV2()
        reg.register(
            _make_def("bad-output", output_schema={"type": "string"}),
            _ok_tool,
        )
        result = await reg.execute("bad-output", {})
        assert not result.success
        assert result.error == "tool output failed schema validation"
        assert result.output == {"invalid_output": True}


class TestInvocationLog:
    @pytest.mark.asyncio
    async def test_invocation_recorded(self):
        reg = ToolRegistryV2()
        reg.register(_make_def("ok"), _ok_tool)
        await reg.execute("ok", {})
        invocations = reg.invocations()
        assert len(invocations) == 1
        inv = invocations[0]
        assert inv.tool_name == "ok"
        assert inv.success is True
        assert inv.duration_ms is not None
        assert inv.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_failed_invocation_recorded(self):
        reg = ToolRegistryV2()
        reg.register(_make_def("fail"), _fail_tool)
        await reg.execute("fail", {})
        invocations = reg.invocations()
        assert len(invocations) == 1
        assert invocations[0].success is False

    @pytest.mark.asyncio
    async def test_no_secrets_in_invocation(self):
        """Invocation log must not contain API keys."""
        reg = ToolRegistryV2()

        async def leaky_tool(args, *, context=None):
            raise RuntimeError("error with nvapi-SECRETKEY1234567890")

        reg.register(_make_def("leaky"), leaky_tool)
        await reg.execute("leaky", {})
        invocations = reg.invocations()
        assert "SECRETKEY" not in invocations[0].error
        assert "nvapi-[REDACTED]" in invocations[0].error

    def test_safe_error_text_redacts_credentials(self):
        from albra_seed.toolkit.registry import _safe_error_text
        message = "nvapi-SECRETKEY1234567890 sk-SECRETKEY1234567890 Bearer abcdefghijklmnop"
        result = _safe_error_text(message)
        assert "SECRETKEY" not in result
        assert "Bearer abcdefghijklmnop" not in result
        assert "nvapi-[REDACTED]" in result
        assert "sk-[REDACTED]" in result
        assert "Bearer [REDACTED]" in result
    @pytest.mark.asyncio
    async def test_clear_invocations(self):
        reg = ToolRegistryV2()
        reg.register(_make_def("ok"), _ok_tool)
        await reg.execute("ok", {})
        reg.clear_invocations()
        assert len(reg.invocations()) == 0


class TestBuiltinTools:
    @pytest.mark.asyncio
    async def test_echo(self):
        from albra_seed.toolkit.registry import _echo_tool
        result = await _echo_tool({"message": "hello"})
        assert result.success
        assert result.output == {"echoed": "hello"}

    @pytest.mark.asyncio
    async def test_calculator_add(self):
        from albra_seed.toolkit.registry import _calculator_tool
        result = await _calculator_tool({"a": 3, "b": 4, "op": "add"})
        assert result.success
        assert result.output["result"] == 7.0

    @pytest.mark.asyncio
    async def test_calculator_divide_by_zero(self):
        from albra_seed.toolkit.registry import _calculator_tool
        result = await _calculator_tool({"a": 1, "b": 0, "op": "divide"})
        assert not result.success
        assert result.error == "division by zero"
        assert result.output == {"error": True}

    @pytest.mark.asyncio
    async def test_calculator_unknown_op(self):
        from albra_seed.toolkit.registry import _calculator_tool
        result = await _calculator_tool({"a": 1, "b": 2, "op": "power"})
        assert not result.success

    @pytest.mark.asyncio
    async def test_timestamp(self):
        from albra_seed.toolkit.registry import _timestamp_tool
        result = await _timestamp_tool({})
        assert result.success
        assert "timestamp" in result.output


__all__ = [
    "TestRegistration",
    "TestPermissions",
    "TestExecution",
    "TestInvocationLog",
    "TestBuiltinTools",
]
