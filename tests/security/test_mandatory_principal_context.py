"""Adversarial tests for mandatory principal + context validation.

Each test verifies BOTH the decision AND the observable effect (execute_count).
Every rejection must leave execute_count == 0.
"""
from __future__ import annotations

import pytest
from typing import Any

from albra_seed import RunContext, RiskLevel, AutonomyLevel
from albra_seed.core.types import ToolCall
from albra_seed.core.base import ToolContract
from albra_seed.tools.infra_tools import EchoTool
from albra_seed.tools.executor import ToolExecutor
from albra_seed.tools.registry import ToolRegistry
from albra_seed.security.auth_token import (
    AuthorizationToken, AuthorizationIssuer, AuthorizationVerifier,
)
from albra_seed.security.auth import AuthPrincipal


class _SpyTool(EchoTool):
    def __init__(self):
        super().__init__()
        self.execute_count = 0
        self.contract = ToolContract(
            name="spy", description="spy",
            input_schema={"message": {"type": "string"}}, output_schema={},
            risk_level=RiskLevel.LOW, permissions=[], timeout_seconds=30,
            retry_policy={"max": 0}, evidence_required=False,
            is_dry_run_capable=False, is_external=False, availability="available",
        )

    async def execute(self, arguments, *, context=None, dry_run=False):
        self.execute_count += 1
        return await super().execute(arguments, context=context, dry_run=dry_run)


def _setup() -> tuple[ToolRegistry, _SpyTool, AuthorizationIssuer, AuthorizationVerifier, ToolExecutor]:
    registry = ToolRegistry()
    spy = _SpyTool()
    registry.register(spy)
    issuer = AuthorizationIssuer()
    verifier = AuthorizationVerifier(issuer)
    executor = ToolExecutor(registry, auth_verifier=verifier)
    return registry, spy, issuer, verifier, executor


class TestMandatoryPrincipalValidation:
    """Principal absence or mismatch → REJECTED, execute_count == 0."""

    @pytest.mark.asyncio
    async def test_valid_token_with_principal_absent_in_context(self):
        """Token valid but context has no verified_principal → REJECTED."""
        registry, spy, issuer, _, executor = _setup()
        ctx = RunContext(agent_id="a", task="t", execution_id="exec-1", context_id="ctx-1")
        # NO verified_principal set
        token = issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_valid_token_with_different_principal(self):
        """Token for alice, context principal is bob → REJECTED."""
        registry, spy, issuer, _, executor = _setup()
        ctx = RunContext(agent_id="a", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = AuthPrincipal(identity="bob", is_anonymous=False)
        token = issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert spy.execute_count == 0


class TestMandatoryContextValidation:
    """Context absence or mismatch → REJECTED, execute_count == 0."""

    @pytest.mark.asyncio
    async def test_valid_token_with_context_id_absent(self):
        """Token valid but context has no context_id → REJECTED."""
        registry, spy, issuer, _, executor = _setup()
        ctx = RunContext(agent_id="a", task="t", execution_id="exec-1")
        ctx.verified_principal = AuthPrincipal(identity="alice", is_anonymous=False)
        token = issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_valid_token_with_different_context_id(self):
        """Token for ctx-1, context has ctx-2 → REJECTED."""
        registry, spy, issuer, _, executor = _setup()
        ctx = RunContext(agent_id="a", task="t", execution_id="exec-1", context_id="ctx-2")
        ctx.verified_principal = AuthPrincipal(identity="alice", is_anonymous=False)
        token = issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_valid_token_with_different_execution_id(self):
        """Token for exec-1, context has exec-2 → REJECTED."""
        registry, spy, issuer, _, executor = _setup()
        ctx = RunContext(agent_id="a", task="t", execution_id="exec-2", context_id="ctx-1")
        ctx.verified_principal = AuthPrincipal(identity="alice", is_anonymous=False)
        token = issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert spy.execute_count == 0


class TestForgedOrAlteredToken:
    """Forged or altered tokens → REJECTED, execute_count == 0."""

    @pytest.mark.asyncio
    async def test_altered_token_signature_rejected(self):
        """Token with tampered signature → REJECTED."""
        registry, spy, issuer, _, executor = _setup()
        ctx = RunContext(agent_id="a", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = AuthPrincipal(identity="alice", is_anonymous=False)
        token = issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        # Tamper with the signature
        import dataclasses
        tampered = dataclasses.replace(token, signature="0" * 64)
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=tampered)
        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_non_token_object_rejected(self):
        """A non-AuthorizationToken object → REJECTED."""
        registry, spy, issuer, _, executor = _setup()
        ctx = RunContext(agent_id="a", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = AuthPrincipal(identity="alice", is_anonymous=False)
        class _Fake:
            allowed = True
            decision = "allow"
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=_Fake())
        assert result.success is False
        assert spy.execute_count == 0


class TestLegitimateExecution:
    """Valid principal + context + token → EXECUTES exactly once."""

    @pytest.mark.asyncio
    async def test_legitimate_execution_succeeds(self):
        """All fields match → tool executes exactly once."""
        registry, spy, issuer, _, executor = _setup()
        ctx = RunContext(agent_id="a", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = AuthPrincipal(identity="alice", is_anonymous=False)
        token = issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token, call_id="c1")
        assert result.success is True
        assert spy.execute_count == 1, f"Expected 1 execution, got {spy.execute_count}"
