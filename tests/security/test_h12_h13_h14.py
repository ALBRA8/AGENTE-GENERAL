"""Adversarial tests for H-12 (token canonical), H-13 (idempotency scope), H-14 (dedup).

Each test verifies execute_count (observable effect).
"""
from __future__ import annotations

import dataclasses
import pytest

from albra_seed import RunContext, RiskLevel, AutonomyLevel
from albra_seed.core.base import ToolContract
from albra_seed.core.types import ToolCall, AgentResponse
from albra_seed.tools.infra_tools import EchoTool
from albra_seed.tools.executor import ToolExecutor
from albra_seed.tools.registry import ToolRegistry
from albra_seed.security.auth import AuthPrincipal
from albra_seed.security.trusted_principal import (
    TrustedPrincipal, TrustedPrincipalIssuer, TrustedPrincipalVerifier,
)
from albra_seed.security.auth_token import (
    AuthorizationToken, AuthorizationIssuer, AuthorizationVerifier,
)


class _SpyTool(EchoTool):
    def __init__(self, name="spy"):
        super().__init__()
        self.execute_count = 0
        self.contract = ToolContract(
            name=name, description="spy",
            input_schema={"message": {"type": "string"}}, output_schema={},
            risk_level=RiskLevel.LOW, permissions=[], timeout_seconds=30,
            retry_policy={"max": 0}, evidence_required=False,
            is_dry_run_capable=False, is_external=False, availability="available",
        )
    async def execute(self, arguments, *, context=None, dry_run=False):
        self.execute_count += 1
        return await super().execute(arguments, context=context, dry_run=dry_run)


def _setup(name="spy"):
    registry = ToolRegistry()
    spy = _SpyTool(name)
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


def _ctx(exec_id="exec-1", ctx_id="ctx-1", principal="alice", tp_issuer=None):
    ctx = RunContext(agent_id="a", task="t", execution_id=exec_id, context_id=ctx_id)
    if tp_issuer:
        ctx.verified_principal = tp_issuer.seal(AuthPrincipal(identity=principal, is_anonymous=False))
    return ctx


# =====================================================================
# H-12: AuthorizationToken canonical representation
# =====================================================================


class TestTokenCanonical:
    """Token canonicalization must prevent separator collisions."""

    def test_tool_name_with_pipe_no_collision(self):
        """tool_name="a|b" must NOT collide with tool_name="a", principal_id="b"."""
        issuer = AuthorizationIssuer()
        t1 = issuer.issue(tool_name="a|b", principal_id="alice", execution_id="e1", context_id="c1")
        t2 = issuer.issue(tool_name="a", principal_id="b", execution_id="e1", context_id="c1")
        assert t1.signature != t2.signature, "Canonical collision: pipe in tool_name!"

    def test_context_id_with_pipe_no_collision(self):
        """context_id="c|d" must NOT collide with context_id="c", issued_at="d"."""
        issuer = AuthorizationIssuer()
        t1 = issuer.issue(tool_name="echo", principal_id="alice", execution_id="e1", context_id="c|d")
        t2 = issuer.issue(tool_name="echo", principal_id="alice", execution_id="e1", context_id="c")
        assert t1.signature != t2.signature

    @pytest.mark.asyncio
    async def test_altered_tool_name_invalidates_seal(self):
        """Changing tool_name after signing → verification fails."""
        _, spy, auth_issuer, auth_verifier, executor, tp_issuer = _setup()
        ctx = _ctx(tp_issuer=tp_issuer)
        token = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        altered = dataclasses.replace(token, tool_name="echo")
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=altered)
        assert not result.success
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_altered_principal_id_invalidates_seal(self):
        _, spy, auth_issuer, _, executor, tp_issuer = _setup()
        ctx = _ctx(tp_issuer=tp_issuer)
        token = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        altered = dataclasses.replace(token, principal_id="admin")
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=altered)
        assert not result.success
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_altered_execution_id_invalidates_seal(self):
        _, spy, auth_issuer, _, executor, tp_issuer = _setup()
        ctx = _ctx(tp_issuer=tp_issuer)
        token = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        altered = dataclasses.replace(token, execution_id="exec-2")
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=altered)
        assert not result.success
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_legitimate_token_still_valid(self):
        _, spy, auth_issuer, _, executor, tp_issuer = _setup()
        ctx = _ctx(tp_issuer=tp_issuer)
        token = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        result = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token, call_id="c1")
        assert result.success
        assert spy.execute_count == 1


# =====================================================================
# H-13: Idempotency scope
# =====================================================================


class TestIdempotencyScope:
    """Idempotency must be scoped to (call_id, tool, exec, principal)."""

    @pytest.mark.asyncio
    async def test_same_call_id_different_execution_not_deduped(self):
        """Same call_id in two different executions → both execute."""
        _, spy, auth_issuer, _, executor, tp_issuer = _setup()
        ctx1 = _ctx(exec_id="exec-1", tp_issuer=tp_issuer)
        ctx2 = _ctx(exec_id="exec-2", tp_issuer=tp_issuer)
        token1 = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        token2 = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-2", context_id="ctx-1")
        r1 = await executor.execute("spy", {"message": "hi"}, context=ctx1, authorization=token1, call_id="call-X")
        r2 = await executor.execute("spy", {"message": "hi"}, context=ctx2, authorization=token2, call_id="call-X")
        assert r1.success and r2.success
        assert spy.execute_count == 2  # Both executed — different executions

    @pytest.mark.asyncio
    async def test_same_call_id_different_principal_not_deduped(self):
        """Same call_id, different principal → both execute."""
        _, spy, auth_issuer, _, executor, tp_issuer = _setup()
        ctx1 = _ctx(principal="alice", tp_issuer=tp_issuer)
        ctx2 = _ctx(principal="bob", tp_issuer=tp_issuer)
        token1 = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        token2 = auth_issuer.issue(tool_name="spy", principal_id="bob", execution_id="exec-1", context_id="ctx-1")
        r1 = await executor.execute("spy", {"message": "hi"}, context=ctx1, authorization=token1, call_id="call-X")
        r2 = await executor.execute("spy", {"message": "hi"}, context=ctx2, authorization=token2, call_id="call-X")
        assert r1.success and r2.success
        assert spy.execute_count == 2

    @pytest.mark.asyncio
    async def test_same_call_id_different_context_not_deduped(self):
        """Same call_id + same execution + same principal but different context_id → both execute."""
        _, spy, auth_issuer, _, executor, tp_issuer = _setup()
        ctx1 = _ctx(ctx_id="ctx-A", tp_issuer=tp_issuer)
        ctx2 = _ctx(ctx_id="ctx-B", tp_issuer=tp_issuer)
        token1 = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-A")
        token2 = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-B")
        r1 = await executor.execute("spy", {"message": "hi"}, context=ctx1, authorization=token1, call_id="call-X")
        r2 = await executor.execute("spy", {"message": "hi"}, context=ctx2, authorization=token2, call_id="call-X")
        assert r1.success and r2.success
        assert spy.execute_count == 2  # Both executed — different context_id

    @pytest.mark.asyncio
    async def test_same_call_id_same_scope_deduped(self):
        """Same call_id + same scope → second is deduped."""
        _, spy, auth_issuer, _, executor, tp_issuer = _setup()
        ctx = _ctx(tp_issuer=tp_issuer)
        token = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        r1 = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token, call_id="call-X")
        r2 = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token, call_id="call-X")
        assert r1.success
        assert r2.success and r2.output.get("idempotent_skip") is True
        assert spy.execute_count == 1  # Only first executed

    @pytest.mark.asyncio
    async def test_unauthorized_duplicate_not_succeeds(self):
        """A duplicate call without authorization must NOT get idempotent_skip success."""
        _, spy, auth_issuer, _, executor, tp_issuer = _setup()
        ctx = _ctx(tp_issuer=tp_issuer)
        token = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        # First call succeeds
        r1 = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token, call_id="call-X")
        assert r1.success
        # Second call with NO authorization — must NOT succeed via idempotency
        r2 = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=None, call_id="call-X")
        assert not r2.success
        assert spy.execute_count == 1  # Tool not executed again


# =====================================================================
# H-14: Cognitive loop dedup — denied calls not marked as executed
# =====================================================================


class TestCognitiveDedup:
    """Denied calls must not contaminate the dedup set."""

    @pytest.mark.asyncio
    async def test_denied_call_not_marked_as_executed(self):
        """First call denied (no auth), second call with same call_id + valid
        auth → must execute. Verifies denied calls don't poison dedup set."""
        _, spy, auth_issuer, _, executor, tp_issuer = _setup()
        ctx = _ctx(tp_issuer=tp_issuer)

        # First call: NO authorization → REJECTED
        r1 = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=None, call_id="call-X")
        assert not r1.success
        assert spy.execute_count == 0  # spy NOT invoked

        # Second call: SAME call_id + valid authorization → must execute
        token = auth_issuer.issue(tool_name="spy", principal_id="alice", execution_id="exec-1", context_id="ctx-1")
        r2 = await executor.execute("spy", {"message": "hi"}, context=ctx, authorization=token, call_id="call-X")
        assert r2.success, f"Second call should succeed, got: {r2.error}"
        assert spy.execute_count == 1, f"Expected 1 execution, got {spy.execute_count}"
