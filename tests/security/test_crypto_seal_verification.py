"""Cryptographic seal verification tests for TrustedPrincipal.

Every test verifies execute_count (observable effect). All rejections
must leave execute_count == 0.
"""
from __future__ import annotations

import dataclasses
import pytest

from albra_seed import RunContext, RiskLevel
from albra_seed.core.base import ToolContract
from albra_seed.tools.infra_tools import EchoTool
from albra_seed.tools.executor import ToolExecutor
from albra_seed.tools.registry import ToolRegistry
from albra_seed.security.auth import AuthPrincipal
from albra_seed.security.trusted_principal import (
    TrustedPrincipal, TrustedPrincipalIssuer, TrustedPrincipalVerifier,
)
from albra_seed.security.auth_token import AuthorizationIssuer, AuthorizationVerifier


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


def _full_setup():
    """Setup with matching tp_issuer → tp_verifier → executor."""
    registry = ToolRegistry()
    spy = _SpyTool()
    registry.register(spy)
    auth_issuer = AuthorizationIssuer()
    auth_verifier = AuthorizationVerifier(auth_issuer)
    tp_issuer = TrustedPrincipalIssuer()
    tp_verifier = TrustedPrincipalVerifier(tp_issuer)
    executor = ToolExecutor(
        registry, auth_verifier=auth_verifier,
        trusted_principal_verifier=tp_verifier,
    )
    return registry, spy, auth_issuer, auth_verifier, executor, tp_issuer


def _make_ctx(exec_id="exec-1", ctx_id="ctx-1", principal_id="alice"):
    ctx = RunContext(agent_id="a", task="t", execution_id=exec_id, context_id=ctx_id)
    return ctx


class TestSealForgery:
    """Forged TrustedPrincipal with invented seal → REJECTED."""

    @pytest.mark.asyncio
    async def test_trusted_principal_with_invented_seal_rejected(self):
        """Construct TrustedPrincipal directly with a fake seal → REJECTED."""
        registry, spy, auth_issuer, _, executor, tp_issuer = _full_setup()
        ctx = _make_ctx()
        
        # Construct a TrustedPrincipal with an invented seal
        fake = TrustedPrincipal(
            principal_id="admin",
            kind="user",
            roles=("admin",),
            auth_method="noop",
            is_anonymous=False,
            sealed_at=1234567890.0,
            seal="a" * 64,  # invented seal — NOT computed by the issuer
        )
        ctx.verified_principal = fake
        
        token = auth_issuer.issue(
            tool_name="spy", principal_id="admin",
            execution_id="exec-1", context_id="ctx-1",
        )
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert "invalid_seal" in (result.output.get("reason", "") if result.output else "")
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_trusted_principal_with_same_attrs_but_invalid_seal(self):
        """Fabricate a TrustedPrincipal with same attrs as legitimate but invalid seal."""
        registry, spy, auth_issuer, _, executor, tp_issuer = _full_setup()
        ctx = _make_ctx()
        
        # Create a legitimate TrustedPrincipal
        real = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))
        
        # Fabricate a fake with same attrs but wrong seal
        fake = TrustedPrincipal(
            principal_id=real.principal_id,
            kind=real.kind,
            roles=real.roles,
            auth_method=real.auth_method,
            is_anonymous=real.is_anonymous,
            sealed_at=real.sealed_at,
            seal="0" * 64,  # wrong seal
        )
        ctx.verified_principal = fake
        
        token = auth_issuer.issue(
            tool_name="spy", principal_id="alice",
            execution_id="exec-1", context_id="ctx-1",
        )
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_altered_principal_id_in_sealed_object(self):
        """Alter the principal_id of a sealed TrustedPrincipal → REJECTED."""
        registry, spy, auth_issuer, _, executor, tp_issuer = _full_setup()
        ctx = _make_ctx()
        
        real = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))
        # Alter the principal_id (dataclasses.replace keeps the old seal)
        altered = dataclasses.replace(real, principal_id="admin")
        ctx.verified_principal = altered
        
        token = auth_issuer.issue(
            tool_name="spy", principal_id="admin",
            execution_id="exec-1", context_id="ctx-1",
        )
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_altered_roles_in_sealed_object(self):
        """Alter the roles of a sealed TrustedPrincipal → REJECTED."""
        registry, spy, auth_issuer, _, executor, tp_issuer = _full_setup()
        ctx = _make_ctx()
        
        real = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False, roles=[]))
        # Add admin role without re-sealing
        altered = dataclasses.replace(real, roles=("admin",))
        ctx.verified_principal = altered
        
        token = auth_issuer.issue(
            tool_name="spy", principal_id="alice",
            execution_id="exec-1", context_id="ctx-1",
        )
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_verifier_from_different_issuer_rejected(self):
        """TrustedPrincipal sealed by issuer A, verified by issuer B → REJECTED."""
        registry, spy, auth_issuer, _, _, tp_issuer_a = _full_setup()
        
        # Create a DIFFERENT tp_issuer for the executor
        tp_issuer_b = TrustedPrincipalIssuer()
        tp_verifier_b = TrustedPrincipalVerifier(tp_issuer_b)
        
        # Build executor with issuer B's verifier
        auth_verifier = AuthorizationVerifier(auth_issuer)
        executor_with_b = ToolExecutor(
            registry, auth_verifier=auth_verifier,
            trusted_principal_verifier=tp_verifier_b,
        )
        
        ctx = _make_ctx()
        # Seal with issuer A
        ctx.verified_principal = tp_issuer_a.seal(AuthPrincipal(identity="alice", is_anonymous=False))
        
        token = auth_issuer.issue(
            tool_name="spy", principal_id="alice",
            execution_id="exec-1", context_id="ctx-1",
        )
        result = await executor_with_b.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_no_tp_verifier_rejects_all(self):
        """Executor without trusted_principal_verifier → ALL real executions REJECTED."""
        registry, spy, auth_issuer, _, _, tp_issuer = _full_setup()
        
        # Build executor WITHOUT trusted_principal_verifier
        auth_verifier = AuthorizationVerifier(auth_issuer)
        executor_no_tp = ToolExecutor(registry, auth_verifier=auth_verifier)
        # NO trusted_principal_verifier passed
        
        ctx = _make_ctx()
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))
        
        token = auth_issuer.issue(
            tool_name="spy", principal_id="alice",
            execution_id="exec-1", context_id="ctx-1",
        )
        result = await executor_no_tp.execute("spy", {"message": "hi"}, context=ctx, authorization=token)
        assert result.success is False
        assert "no_tp_verifier" in (result.output.get("reason", "") if result.output else "")
        assert spy.execute_count == 0


class TestLegitimateSealedExecution:
    """Valid sealed TrustedPrincipal + valid AuthorizationToken → executes once."""

    @pytest.mark.asyncio
    async def test_legitimate_execution_executes_once(self):
        """All fields valid + seal verified → tool executes exactly once."""
        registry, spy, auth_issuer, _, executor, tp_issuer = _full_setup()
        ctx = _make_ctx()
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))
        
        token = auth_issuer.issue(
            tool_name="spy", principal_id="alice",
            execution_id="exec-1", context_id="ctx-1",
        )
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token, call_id="c1")
        assert result.success is True
        assert spy.execute_count == 1, f"Expected 1 execution, got {spy.execute_count}"
