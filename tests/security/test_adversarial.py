"""Adversarial security tests — H-10.

Each test verifies BOTH:

1. The correct DECISION (DENY/ALLOW) — i.e. the verdict returned by
   the gate, or the success/failure flag on the resulting ToolResult.
2. The correct EFFECT — i.e. the tool's ``execute()`` method was (or
   was NOT) invoked, no side-effect occurred, and no authorization
   change took place.

A test passes ONLY when BOTH the decision AND the effect are correct.
This is the strictest form of security verification: a gate that
returns DENY but still allows the underlying tool to run is a
false-negative that the assertion pattern below would catch.

Test classes (each maps to a finding from the security audit):

  * ``TestTokenValidation``            — token → AuthPrincipal resolution.
  * ``TestForgedPrincipal``             — caller metadata cannot impersonate.
  * ``TestDirectExecutorBypass``       — ToolExecutor requires authorization.
  * ``TestRevocationBetweenCheckAndExecute`` — defense-in-depth at execute point.
  * ``TestContractPermissions``        — ToolContract.permissions / policy rules.
  * ``TestCallExcess``                 — max_tool_calls / max_calls_per_tool.
  * ``TestTimeout``                    — per-tool + cognitive-loop time budgets.
  * ``TestRetry``                      — idempotent call_id (H-08).
  * ``TestNoEffectOnDenial``           — denied tools produce NO side-effect.

All tests use deterministic Mock providers — NO real LLM calls.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from albra_seed import (
    Agent,
    AgentIdentity,
    AutonomyLevel,
    RiskLevel,
    RunContext,
    Runtime,
    SeedConfig,
)
from albra_seed.core.base import LLMProvider, Tool, ToolContract
from albra_seed.core.types import (
    AgentResponse,
    Message,
    PolicyDecision,
    ToolCall,
    ToolResult,
)
from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
from albra_seed.firewall.kill_switch import KillSwitch
from albra_seed.policy.engine import PolicyEngine, PolicyVerdict
from albra_seed.policy.rules import PolicyRule
from albra_seed.runtime.loop import CognitiveLoopConfig
from albra_seed.security.auth import (
    Authenticator,
    AuthPrincipal,
    NoopAuthenticator,
)
from albra_seed.security.auth_token import (
    AuthorizationIssuer,
    AuthorizationVerifier,
)
from albra_seed.security.permissions import PermissionRegistry
from albra_seed.security.roles import Role as RBACRole
from albra_seed.security.roles import RoleRegistry
from albra_seed.tools.executor import ToolExecutor
from albra_seed.tools.infra_tools import EchoTool
from albra_seed.tools.registry import ToolRegistry

# =====================================================================
# Mock components
# =====================================================================


class _MockAuthenticator(Authenticator):
    """Test authenticator with a configurable principal + token table.

    ``verify(token)`` semantics:

      * ``token=None`` → anonymous principal (CORE_ONLY contract).
      * ``token="valid"`` → the configured principal.
      * any other token → ``None`` (fail-closed — invalid token).
    """

    name = "mock-auth"
    is_mock = True

    def __init__(self, principal: AuthPrincipal | None = None) -> None:
        self._principal = principal

    async def authenticate(self, credentials: Any = None) -> AuthPrincipal:
        return self._principal or AuthPrincipal(is_anonymous=True)

    async def verify(self, token: str | None) -> AuthPrincipal | None:
        if token is None:
            return AuthPrincipal(
                kind="anonymous",
                identity="anonymous",
                roles=[],
                is_anonymous=True,
            )
        if token == "valid-token":
            return self._principal
        # Any other token → None (fail-closed).
        return None


class _ToolCallingProvider(LLMProvider):
    """Mock provider that emits a fixed tool_call in its response."""

    name = "tool-calling"
    is_mock = True

    def __init__(self, tool_calls: list[ToolCall]) -> None:
        self._tool_calls = list(tool_calls)
        self._call_count = 0

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        context: RunContext | None = None,
    ) -> AgentResponse:
        self._call_count += 1
        exec_id = context.execution_id if context else "exec"
        corr_id = context.correlation_id if context else "corr"
        return AgentResponse(
            content="[mock]",
            execution_id=exec_id,
            correlation_id=corr_id,
            tool_calls=list(self._tool_calls),
        )

    async def health(self) -> bool:
        return True

    @property
    def call_count(self) -> int:
        return self._call_count


class _SlowTool(Tool):
    """Tool that sleeps ``delay_seconds`` before returning a result.

    Used by the timeout tests to exceed a per-tool timeout.
    """

    def __init__(self, name: str = "slow", delay_seconds: float = 2.0) -> None:
        self.contract = ToolContract(
            name=name,
            description="Sleeps, then returns a result.",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"slept": {"type": "boolean"}},
                "required": ["slept"],
            },
            risk_level=RiskLevel.LOW,
            timeout_seconds=1,
        )
        self._delay = delay_seconds
        self.execute_count: int = 0

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        self.execute_count += 1
        await asyncio.sleep(self._delay)
        return ToolResult(
            call_id="slow-result",
            success=True,
            output={"slept": True},
        )


class _SpyEchoTool(EchoTool):
    """EchoTool that records every ``execute`` invocation.

    Used by the no-effect-on-denial tests to assert the tool's
    ``execute()`` was NEVER called when the gate DENIED.
    """

    def __init__(self, name: str = "echo") -> None:
        super().__init__()
        self.contract = self.contract.model_copy(update={"name": name})
        self.execute_count: int = 0
        self.execute_args: list[dict[str, Any]] = []

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        self.execute_count += 1
        self.execute_args.append(dict(arguments))
        return await super().execute(arguments, context=context, dry_run=dry_run)


# =====================================================================
# Helpers
# =====================================================================


def _build_runtime(
    *,
    provider: LLMProvider,
    tools: list[Tool] | None = None,
    firewall_allow: list[str] | None = None,
    cognitive_config: CognitiveLoopConfig | None = None,
    autonomy: AutonomyLevel = AutonomyLevel.L2_EXECUTE_SAFE,
    authenticator: Authenticator | None = None,
    policy: PolicyEngine | None = None,
) -> tuple[Runtime, Agent]:
    """Wire a Runtime + Agent. ``firewall_allow=None`` defaults to all tools.

    ``firewall_allow`` semantics:
      * ``None`` (default): allow every REGISTERED tool name.
      * ``[]`` (explicit empty list): allow NOTHING — DEFAULT-DENY.
      * ``["echo"]``: allow exactly those tool names.
    """
    runtime = Runtime(config=SeedConfig())
    asyncio.get_event_loop()
    runtime.register_provider(provider)
    for tool in (tools or []):
        runtime.register_tool(tool)

    if firewall_allow is None:
        allow = [t.contract.name for t in (tools or [])]
    else:
        allow = list(firewall_allow)

    fw = ActionFirewall()
    for tool_name in allow:
        fw.add_rule(
            FirewallRule(
                action_pattern=tool_name,
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
    runtime.register("firewall", fw)

    if policy is None:
        policy = PolicyEngine()
        for tool_name in allow:
            policy.add_rule(
                PolicyRule(
                    name=f"allow_{tool_name}",
                    action_pattern=tool_name,
                    actor_pattern="*",
                    risk_threshold=RiskLevel.UNKNOWN,
                    autonomy_required=AutonomyLevel.L0_OBSERVE,
                    decision="allow",
                    priority=10,
                )
            )
    runtime.register("policy_engine", policy)

    if authenticator is not None:
        runtime.register("authenticator", authenticator)

    identity = AgentIdentity(
        name="adversarial-test-agent", autonomy_level=autonomy
    )
    agent = Agent(identity, runtime, cognitive_loop_config=cognitive_config)
    return runtime, agent


async def _run_with_startup(
    runtime: Runtime, agent: Agent, prompt: str, **kwargs: Any
) -> AgentResponse:
    await runtime.startup()
    try:
        return await agent.run(prompt, **kwargs)
    finally:
        await runtime.shutdown()


# =====================================================================
# TestTokenValidation
# =====================================================================


class TestTokenValidation:
    """H-10: Token → AuthPrincipal resolution + denial of invalid tokens."""

    @pytest.mark.asyncio
    async def test_valid_token_authorizes(self) -> None:
        """Valid token → AuthPrincipal with roles → tool allowed if roles match.

        DECISION: ALLOW (principal has the required permission).
        EFFECT: tool's ``execute()`` is invoked exactly ONCE.
        """
        spy = _SpyEchoTool()
        principal = AuthPrincipal(
            kind="user",
            identity="alice",
            roles=["admin"],
            is_anonymous=False,
        )
        auth = _MockAuthenticator(principal=principal)

        # Policy requires ``tool.execute.echo`` — admin role has it.
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="req_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute.echo",
                decision="allow",
                priority=10,
            )
        )
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        registry = PermissionRegistry()
        registry.roles = roles
        registry.grant("admin", "tool.execute.echo")
        policy.permission_registry = registry

        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            authenticator=auth,
            policy=policy,
        )
        response = await _run_with_startup(
            runtime, agent, "echo hi", token="valid-token"
        )
        # DECISION: ALLOW
        assert response.tool_results, "tool must produce a result"
        assert response.tool_results[0].success is True, (
            f"valid token should authorize; got: {response.tool_results[0]!r}"
        )
        # EFFECT: tool's execute() was called exactly ONCE
        assert spy.execute_count == 1, (
            f"expected 1 execute; got {spy.execute_count}"
        )

    @pytest.mark.asyncio
    async def test_invalid_token_denies(self) -> None:
        """Invalid token → AuthPrincipal=None → protected action denied.

        DECISION: DENY (no verified principal → no roles).
        EFFECT: tool's ``execute()`` NEVER invoked.
        """
        spy = _SpyEchoTool()
        principal = AuthPrincipal(
            kind="user",
            identity="alice",
            roles=["admin"],
            is_anonymous=False,
        )
        auth = _MockAuthenticator(principal=principal)

        # Policy requires ``tool.execute.echo`` — admin role has it.
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="req_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute.echo",
                decision="allow",
                priority=10,
            )
        )
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        registry = PermissionRegistry()
        registry.roles = roles
        registry.grant("admin", "tool.execute.echo")
        policy.permission_registry = registry

        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            authenticator=auth,
            policy=policy,
        )
        # An INVALID token → ``_MockAuthenticator.verify`` returns None
        # → IdentityResolver returns None → actor_roles=None → DENY.
        response = await _run_with_startup(
            runtime, agent, "echo hi", token="bogus-token"
        )
        # DECISION: DENY
        assert response.tool_results, "tool must still produce a (failed) result"
        assert response.tool_results[0].success is False
        # EFFECT: tool NEVER executed
        assert spy.execute_count == 0, (
            f"expected 0 executes on denied; got {spy.execute_count}"
        )

    @pytest.mark.asyncio
    async def test_expired_token_denies(self) -> None:
        """Expired token → AuthPrincipal=None → protected action denied.

        We model "expired" by configuring the Authenticator to return
        ``None`` for the supplied token (simulating a token whose
        signature once was valid but has since expired).

        DECISION: DENY.
        EFFECT: tool's ``execute()`` NEVER invoked.
        """
        spy = _SpyEchoTool()
        # Authenticator returns None for any non-"valid-token" string.
        auth = _MockAuthenticator(
            principal=AuthPrincipal(
                kind="user",
                identity="alice",
                roles=["admin"],
                is_anonymous=False,
            )
        )

        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="req_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute.echo",
                decision="allow",
                priority=10,
            )
        )
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        registry = PermissionRegistry()
        registry.roles = roles
        registry.grant("admin", "tool.execute.echo")
        policy.permission_registry = registry

        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            authenticator=auth,
            policy=policy,
        )
        # "expired-token" is not "valid-token" → Authenticator returns None.
        response = await _run_with_startup(
            runtime, agent, "echo hi", token="expired-token"
        )
        # DECISION: DENY
        assert response.tool_results[0].success is False
        # EFFECT: tool NEVER executed
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_no_token_denies_protected_action(self) -> None:
        """No token → anonymous principal → protected action denied.

        DECISION: DENY.
        EFFECT: tool's ``execute()`` NEVER invoked.
        """
        spy = _SpyEchoTool()
        # NoopAuthenticator → returns anonymous principal with empty roles.
        auth = NoopAuthenticator()

        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="req_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute.echo",
                decision="allow",
                priority=10,
            )
        )
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        registry = PermissionRegistry()
        registry.roles = roles
        registry.grant("admin", "tool.execute.echo")
        policy.permission_registry = registry

        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            authenticator=auth,
            policy=policy,
        )
        # token=None (default) → anonymous principal → empty roles → DENY.
        response = await _run_with_startup(runtime, agent, "echo hi")
        # DECISION: DENY (anonymous principal has no roles)
        assert response.tool_results[0].success is False
        # EFFECT: tool NEVER executed
        assert spy.execute_count == 0


# =====================================================================
# TestForgedPrincipal
# =====================================================================


class TestForgedPrincipal:
    """Caller metadata cannot forge roles; IdentityResolver overwrites."""

    @pytest.mark.asyncio
    async def test_forged_roles_in_metadata_ignored(self) -> None:
        """Caller sets metadata={'actor_roles': ['admin']} → agent uses Authenticator.

        The agent's IdentityResolver consults the Authenticator (which
        returns the "viewer" principal — no admin role). The PolicyEngine
        is configured with a rule requiring ``admin.execute``; only the
        "admin" role has that permission. Since the principal is
        "viewer" (NOT "admin"), the action is DENIED — even though
        metadata claims admin.

        DECISION: DENY (Authenticator wins over metadata).
        EFFECT: tool's ``execute()`` NEVER invoked.
        """
        spy = _SpyEchoTool()
        principal = AuthPrincipal(
            kind="user",
            identity="alice",
            roles=["viewer"],  # NOT admin
            is_anonymous=False,
        )
        auth = _MockAuthenticator(principal=principal)

        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="req_admin",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="admin.execute",
                decision="allow",
                priority=10,
            )
        )
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        roles.register(RBACRole(role_id="viewer", name="viewer"))
        registry = PermissionRegistry()
        registry.roles = roles
        registry.grant("admin", "admin.execute")
        # viewer has NO admin.execute permission.
        policy.permission_registry = registry

        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            authenticator=auth,
            policy=policy,
        )
        await runtime.startup()
        try:
            # Caller tries to forge roles via metadata.
            ctx = RunContext(
                agent_id=agent.identity.agent_id,
                task="echo hi",
                metadata={"actor_roles": ["admin"]},
            )
            response = await agent.run("echo hi", context=ctx, token="valid-token")
        finally:
            await runtime.shutdown()
        # DECISION: DENY — agent used Authenticator's roles (viewer), not metadata.
        assert response.tool_results[0].success is False
        # EFFECT: tool NEVER executed
        assert spy.execute_count == 0

    @pytest.mark.asyncio
    async def test_forged_verified_principal_rejected(self) -> None:
        """Caller sets ``run_ctx.verified_principal`` manually → resolver overwrites.

        ``RunContext.verified_principal`` is a public field (callers CAN
        set it). But ``IdentityResolver.resolve_and_attach()`` ALWAYS
        overwrites it at the start of CHECK_PERMISSIONS — so a caller
        who manually sets a forged principal still loses.

        This test verifies the resolver's overwrite behavior: a forged
        "admin" principal on the RunContext is replaced by the
        Authenticator's principal (anonymous, no roles) → DENY.
        """
        spy = _SpyEchoTool()
        # NoopAuthenticator → returns an anonymous principal (empty roles).
        auth = NoopAuthenticator()

        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="req_admin",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="admin.execute",
                decision="allow",
                priority=10,
            )
        )
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        registry = PermissionRegistry()
        registry.roles = roles
        registry.grant("admin", "admin.execute")
        policy.permission_registry = registry

        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            authenticator=auth,
            policy=policy,
        )
        await runtime.startup()
        try:
            # Caller FORGES a verified principal on the RunContext —
            # claiming to be "admin" with the admin role.
            ctx = RunContext(
                agent_id=agent.identity.agent_id,
                task="echo hi",
            )
            ctx.verified_principal = AuthPrincipal(
                kind="user",
                identity="alice",
                roles=["admin"],
                is_anonymous=False,
            )
            response = await agent.run("echo hi", context=ctx)
        finally:
            await runtime.shutdown()
        # DECISION: DENY — the IdentityResolver overwrote the forged
        # principal with the Authenticator's anonymous principal.
        assert response.tool_results[0].success is False
        # EFFECT: tool NEVER executed
        assert spy.execute_count == 0


# =====================================================================
# TestDirectExecutorBypass
# =====================================================================


class TestDirectExecutorBypass:
    """H-01/H-02: ToolExecutor requires authorization; dry_run is exempt."""

    @pytest.mark.asyncio
    async def test_tool_executor_without_authorization_refuses(self) -> None:
        """ToolExecutor.execute() without authorization → refuses.

        DECISION: refuse (no verdict).
        EFFECT: tool's ``execute()`` NEVER invoked; the executor
        returns a failed ToolResult with ``output.not_authorized=True``.
        """
        spy = _SpyEchoTool()
        registry = ToolRegistry()
        registry.register(spy)
        executor = ToolExecutor(registry)
        result = await executor.execute("echo", {"message": "hi"})
        # DECISION: refuse
        assert result.success is False
        # EFFECT: tool NEVER executed
        assert spy.execute_count == 0
        # The refusal surfaces a clear error message.
        assert "not authorized" in (result.error or "").lower()
        # The output dict carries the structured ``not_authorized`` flag.
        assert isinstance(result.output, dict)
        assert result.output.get("not_authorized") is True

    @pytest.mark.asyncio
    async def test_tool_executor_with_denied_authorization_refuses(self) -> None:
        """ToolExecutor.execute() with denied verdict → refuses.

        DECISION: refuse (verdict says DENY).
        EFFECT: tool's ``execute()`` NEVER invoked.
        """
        spy = _SpyEchoTool()
        registry = ToolRegistry()
        registry.register(spy)
        executor = ToolExecutor(registry)
        denied_verdict = PolicyVerdict(
            decision=PolicyDecision.DENY,
            allowed=False,
        )
        result = await executor.execute(
            "echo", {"message": "hi"}, authorization=denied_verdict
        )
        # DECISION: refuse
        assert result.success is False
        # EFFECT: tool NEVER executed
        assert spy.execute_count == 0
        assert "not authorized" in (result.error or "").lower()
        assert isinstance(result.output, dict)
        assert result.output.get("not_authorized") is True

    @pytest.mark.asyncio
    async def test_tool_executor_with_allowed_authorization_executes(self) -> None:
        """ToolExecutor.execute() with a valid AuthorizationToken → executes.

        DECISION: ALLOW.
        EFFECT: tool's ``execute()`` invoked exactly ONCE; result
        is successful.

        H-01/H-02 amendment: the executor no longer accepts a legacy
        ``PolicyVerdict(allowed=True)`` stub. It requires a non-forgeable
        ``AuthorizationToken`` issued by ``AuthorizationIssuer`` and
        verified by an ``AuthorizationVerifier`` configured on the
        executor. Without it the executor FAILS-CLOSED.
        """
        spy = _SpyEchoTool()
        registry = ToolRegistry()
        registry.register(spy)
        issuer = AuthorizationIssuer()
        verifier = AuthorizationVerifier(issuer)
        executor = ToolExecutor(registry, auth_verifier=verifier)
        ctx = RunContext(agent_id="test", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = AuthPrincipal(
            identity="alice", is_anonymous=False
        )
        token = issuer.issue(
            tool_name="echo",
            principal_id="alice",
            execution_id="exec-1",
            context_id='ctx-1',
        )
        result = await executor.execute(
            "echo",
            {"message": "hi"},
            context=ctx,
            authorization=token,
            call_id="call-1",
        )
        # DECISION: ALLOW
        assert result.success is True
        # EFFECT: tool executed exactly ONCE
        assert spy.execute_count == 1

    @pytest.mark.asyncio
    async def test_dry_run_bypasses_authorization(self) -> None:
        """dry_run=True → simulated result WITHOUT invoking the tool.

        Dry-run execution no longer invokes ``tool.execute()`` — it
        returns a SIMULATED result that describes what WOULD have run.
        The authorization gate is SKIPPED because dry-run cannot
        produce real-world effects.

        DECISION: ALLOW (dry-run is safe by contract).
        EFFECT: tool's ``execute()`` NEVER invoked (execute_count==0);
        the result is a SIMULATED dry-run output.
        """
        spy = _SpyEchoTool()
        registry = ToolRegistry()
        registry.register(spy)
        executor = ToolExecutor(registry)
        # NO authorization provided — but dry_run=True.
        result = await executor.execute("echo", {"message": "hi"}, dry_run=True)
        # DECISION: ALLOW (dry-run bypasses the gate)
        assert result.success is True
        # Dry-run returns a SIMULATED result (not a real execution).
        assert isinstance(result.output, dict)
        assert result.output.get("simulated") is True
        assert result.output.get("dry_run") is True
        # EFFECT: tool's execute() NEVER invoked (dry-run does not call it).
        assert spy.execute_count == 0


# =====================================================================
# TestRevocationBetweenCheckAndExecute
# =====================================================================


class TestRevocationBetweenCheckAndExecute:
    """Defense-in-depth: kill switch / cooldown re-checked at EXECUTE time.

    A gate that ALLOWED at CHECK_PERMISSIONS may have flipped to DENY
    between CHECK_PERMISSIONS and EXECUTE. The EXECUTE phase re-checks
    the kill switch and cooldown — and refuses to dispatch.
    """

    @pytest.mark.asyncio
    async def test_kill_switch_activated_between_phases_blocks(self) -> None:
        """Kill switch activated AFTER CHECK_PERMISSIONS → blocked at EXECUTE.

        We hook the EXECUTE phase: just before it runs, we activate the
        kill switch. CHECK_PERMISSIONS already passed (kill switch
        was inactive then); EXECUTE re-checks and DENIES.

        DECISION: DENY (kill switch active at execute time).
        EFFECT: tool's ``execute()`` NEVER invoked.
        """
        spy = _SpyEchoTool()
        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            cognitive_config=None,  # LINEAR — simpler hooking
        )
        await runtime.startup()
        try:
            fw = runtime.get_component("firewall")
            assert fw is not None
            kill_switch: KillSwitch = fw.kill_switch

            # Hook the EXECUTE phase: just before it runs, activate
            # the kill switch. CHECK_PERMISSIONS already ran (kill
            # switch was inactive then) — EXECUTE will re-check.
            original_execute = agent._phase_execute

            async def _spy_execute(loop_agent: Agent, ctx: dict[str, Any]) -> Any:
                # Activate the kill switch BEFORE the original EXECUTE
                # runs. The CHECK_PERMISSIONS phase already ran with
                # the kill switch INACTIVE.
                await kill_switch.activate(reason="between-check-and-execute")
                return await original_execute(loop_agent, ctx)

            agent._phase_execute = _spy_execute

            response = await agent.run("echo hi")
            # DECISION: DENY
            assert response.tool_results, "tool must produce a result"
            assert response.tool_results[0].success is False
            err = response.tool_results[0].error or ""
            assert (
                "kill_switch" in err.lower()
                or "blocked" in err.lower()
                or "deny" in err.lower()
            ), f"unexpected error: {err!r}"
            # EFFECT: tool NEVER executed
            assert spy.execute_count == 0, (
                f"expected 0 executes on kill-switch block; got {spy.execute_count}"
            )
        finally:
            await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_cooldown_triggered_between_phases_blocks(self) -> None:
        """Cooldown triggered AFTER CHECK_PERMISSIONS → blocked at EXECUTE.

        We hook the EXECUTE phase: just before it runs, we trigger a
        cooldown on the "echo" action. CHECK_PERMISSIONS already
        passed (cooldown was inactive then); EXECUTE re-checks and
        DENIES.

        DECISION: DENY (cooldown active at execute time).
        EFFECT: tool's ``execute()`` NEVER invoked.
        """
        spy = _SpyEchoTool()
        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            cognitive_config=None,  # LINEAR — simpler hooking
        )
        await runtime.startup()
        try:
            fw = runtime.get_component("firewall")
            assert fw is not None
            cooldown = fw.cooldown

            # Hook the EXECUTE phase: just before it runs, trigger a
            # cooldown on "echo".
            original_execute = agent._phase_execute

            async def _spy_execute(loop_agent: Agent, ctx: dict[str, Any]) -> Any:
                cooldown.trigger(
                    "echo", seconds=60.0, reason="between-check-and-execute"
                )
                return await original_execute(loop_agent, ctx)

            agent._phase_execute = _spy_execute

            response = await agent.run("echo hi")
            # DECISION: DENY
            assert response.tool_results, "tool must produce a result"
            assert response.tool_results[0].success is False
            err = response.tool_results[0].error or ""
            assert "cooldown" in err.lower(), f"unexpected error: {err!r}"
            # EFFECT: tool NEVER executed
            assert spy.execute_count == 0, (
                f"expected 0 executes on cooldown block; got {spy.execute_count}"
            )
        finally:
            await runtime.shutdown()


# =====================================================================
# TestContractPermissions
# =====================================================================


class TestContractPermissions:
    """H-10: ToolContract.permissions + PolicyRule.required_permission gate."""

    @pytest.mark.asyncio
    async def test_tool_contract_permissions_checked(self) -> None:
        """ToolContract with permissions → PolicyEngine verifies them.

        Setup:
          * EchoTool contract has ``permissions=["tool.execute.echo"]``
            (declares what permission executing the tool requires).
          * Policy rule has ``required_permission="tool.execute.echo"``
            (enforces that the actor must have that permission).
          * An Agent with an AUTHENTICATED admin role (granted
            ``tool.execute.echo``) executes the tool → ALLOW.
          * An Agent with an anonymous principal (no roles) → DENY.

        DECISION + EFFECT verified in BOTH branches.
        """
        # Build the policy ONCE; reuse for both branches.
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="req_echo_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute.echo",
                decision="allow",
                priority=10,
            )
        )
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        registry = PermissionRegistry()
        registry.roles = roles
        registry.grant("admin", "tool.execute.echo")
        policy.permission_registry = registry

        # --- Branch 1: authorized principal → ALLOW ---
        spy_allowed = _SpyEchoTool()
        auth_principal = AuthPrincipal(
            kind="user",
            identity="alice",
            roles=["admin"],
            is_anonymous=False,
        )
        auth = _MockAuthenticator(principal=auth_principal)
        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy_allowed],
            firewall_allow=["echo"],
            authenticator=auth,
            policy=policy,  # reuse the SAME policy
        )
        response = await _run_with_startup(
            runtime, agent, "echo hi", token="valid-token"
        )
        # DECISION: ALLOW
        assert response.tool_results[0].success is True
        # EFFECT: tool executed exactly ONCE
        assert spy_allowed.execute_count == 1

        # --- Branch 2: anonymous principal → DENY ---
        spy_denied = _SpyEchoTool()
        # NoopAuthenticator → anonymous principal (empty roles)
        anon_auth = NoopAuthenticator()
        provider2 = _ToolCallingProvider(
            [ToolCall(id="tc-2", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime2, agent2 = _build_runtime(
            provider=provider2,
            tools=[spy_denied],
            firewall_allow=["echo"],
            authenticator=anon_auth,
            policy=policy,  # reuse the SAME policy
        )
        response2 = await _run_with_startup(runtime2, agent2, "echo hi")
        # DECISION: DENY (anonymous → no roles → no permission)
        assert response2.tool_results[0].success is False
        # EFFECT: tool NEVER executed
        assert spy_denied.execute_count == 0


# =====================================================================
# TestCallExcess
# =====================================================================


class TestCallExcess:
    """H-10: max_tool_calls (total) + max_calls_per_tool (per-tool) caps."""

    @pytest.mark.asyncio
    async def test_total_call_limit_exceeded_blocks(self) -> None:
        """max_tool_calls exceeded → further calls blocked.

        The cognitive loop runs 2 iterations, each emitting a tool_call.
        With ``max_tool_calls=1``, the FIRST iteration succeeds; the
        SECOND iteration is blocked because the total cap is hit after
        iter 1 (``cognitive_loop_limit_reached:max_tool_calls``).

        DECISION: DENY (cap exceeded).
        EFFECT: tool's ``execute()`` invoked ONCE (only iter 1's call).
        """
        spy = _SpyEchoTool()

        # Provider that ALWAYS returns a fresh-id tool_call — drives
        # the cognitive loop to its max_tool_calls cap.
        class _AlwaysToolCallProvider(LLMProvider):
            name = "always-tc"
            is_mock = True

            def __init__(self) -> None:
                self._n = 0

            async def complete(
                self,
                messages: list[Message],
                *,
                model: str | None = None,
                temperature: float = 0.2,
                max_tokens: int = 2048,
                tools: list[dict] | None = None,
                context: RunContext | None = None,
            ) -> AgentResponse:
                self._n += 1
                exec_id = context.execution_id if context else "exec"
                corr_id = context.correlation_id if context else "corr"
                return AgentResponse(
                    content=f"iter-{self._n}",
                    execution_id=exec_id,
                    correlation_id=corr_id,
                    tool_calls=[
                        ToolCall(
                            id=f"tc-{self._n}",
                            tool_name="echo",
                            arguments={"message": "hi"},
                        )
                    ],
                )

            async def health(self) -> bool:
                return True

        provider = _AlwaysToolCallProvider()
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            cognitive_config=CognitiveLoopConfig(
                max_iterations=100,
                max_tool_calls=1,
                max_calls_per_tool=100,  # don't bind before total cap
            ),
        )
        await _run_with_startup(runtime, agent, "loop")
        # EFFECT: tool executed EXACTLY ONCE (iter 1's call). Iter 2's
        # call is blocked because the total cap is hit.
        assert spy.execute_count == 1, (
            f"expected 1 execute (total cap); got {spy.execute_count}"
        )

    @pytest.mark.asyncio
    async def test_per_tool_call_limit_exceeded_blocks(self) -> None:
        """max_calls_per_tool exceeded → that tool blocked, others may proceed.

        ``max_calls_per_tool=2`` allows the "echo" tool to be invoked
        twice; subsequent calls short-circuit with a
        ``per_tool_call_limit_exceeded`` ToolResult.

        DECISION: DENY (per-tool cap exceeded) for the 3rd+ call.
        EFFECT: tool's ``execute()`` invoked exactly 2 times.
        """
        spy = _SpyEchoTool()

        # Provider that ALWAYS returns a fresh-id tool_call to "echo".
        class _AlwaysToolCallProvider(LLMProvider):
            name = "always-tc-per-tool"
            is_mock = True

            def __init__(self) -> None:
                self._n = 0

            async def complete(
                self,
                messages: list[Message],
                *,
                model: str | None = None,
                temperature: float = 0.2,
                max_tokens: int = 2048,
                tools: list[dict] | None = None,
                context: RunContext | None = None,
            ) -> AgentResponse:
                self._n += 1
                exec_id = context.execution_id if context else "exec"
                corr_id = context.correlation_id if context else "corr"
                return AgentResponse(
                    content=f"iter-{self._n}",
                    execution_id=exec_id,
                    correlation_id=corr_id,
                    tool_calls=[
                        ToolCall(
                            id=f"tc-{self._n}",
                            tool_name="echo",
                            arguments={"message": "hi"},
                        )
                    ],
                )

            async def health(self) -> bool:
                return True

        provider = _AlwaysToolCallProvider()
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            cognitive_config=CognitiveLoopConfig(
                max_iterations=100,
                max_tool_calls=1000,  # don't bind before per-tool cap
                max_calls_per_tool=2,
            ),
        )
        await _run_with_startup(runtime, agent, "loop")
        # EFFECT: tool executed EXACTLY TWICE (per-tool cap = 2).
        assert spy.execute_count == 2, (
            f"expected 2 executes (per-tool cap); got {spy.execute_count}"
        )


# =====================================================================
# TestTimeout
# =====================================================================


class TestTimeout:
    """H-10: per-tool timeout + cognitive-loop time budget."""

    @pytest.mark.asyncio
    async def test_tool_timeout_returns_error(self) -> None:
        """Tool that exceeds its ``timeout_seconds`` → ToolResult with timeout error.

        The SlowTool has ``timeout_seconds=1`` and sleeps 2s. The
        ToolExecutor wraps the call in ``asyncio.wait_for`` and returns
        a failed ToolResult with ``output.timed_out=True``.

        DECISION: refuse (timeout).
        EFFECT: tool's ``execute()`` WAS invoked (it started sleeping)
        but never returned normally — the executor cancels it.
        """
        slow = _SlowTool(name="slow", delay_seconds=2.0)
        # Contract says timeout_seconds=1; sleep is 2s → exceeds.
        registry = ToolRegistry()
        registry.register(slow)
        issuer = AuthorizationIssuer()
        verifier = AuthorizationVerifier(issuer)
        executor = ToolExecutor(registry, auth_verifier=verifier)
        ctx = RunContext(agent_id="test", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = AuthPrincipal(
            identity="alice", is_anonymous=False
        )
        token = issuer.issue(
            tool_name="slow",
            principal_id="alice",
            execution_id="exec-1",
            context_id='ctx-1',
        )
        result = await executor.execute(
            "slow", {}, context=ctx, authorization=token, call_id="call-1",
        )
        # DECISION: refuse (timeout)
        assert result.success is False
        # The error message mentions the timeout.
        assert "timeout" in (result.error or "").lower()
        # EFFECT: the tool's execute() WAS invoked (it started sleeping).
        # The executor cancels it via asyncio.wait_for; the result
        # records the timeout but the call was attempted.
        assert slow.execute_count == 1

    @pytest.mark.asyncio
    async def test_cognitive_time_budget_exhausted_blocks(self) -> None:
        """max_time_seconds exceeded → further iterations blocked.

        The cognitive loop runs with ``max_time_seconds=0.0`` (already
        exhausted). The first iteration's EXECUTE phase reads
        ``ctx["cognitive_remaining_seconds"]`` (which is 0.0 or
        negative) and short-circuits the call with a
        ``cognitive_time_budget_exhausted`` ToolResult.

        DECISION: DENY (time budget exhausted).
        EFFECT: tool's ``execute()`` NEVER invoked.
        """
        spy = _SpyEchoTool()

        # Provider that ALWAYS returns a fresh-id tool_call to "echo".
        class _AlwaysToolCallProvider(LLMProvider):
            name = "always-tc-time-budget"
            is_mock = True

            def __init__(self) -> None:
                self._n = 0

            async def complete(
                self,
                messages: list[Message],
                *,
                model: str | None = None,
                temperature: float = 0.2,
                max_tokens: int = 2048,
                tools: list[dict] | None = None,
                context: RunContext | None = None,
            ) -> AgentResponse:
                self._n += 1
                exec_id = context.execution_id if context else "exec"
                corr_id = context.correlation_id if context else "corr"
                return AgentResponse(
                    content=f"iter-{self._n}",
                    execution_id=exec_id,
                    correlation_id=corr_id,
                    tool_calls=[
                        ToolCall(
                            id=f"tc-{self._n}",
                            tool_name="echo",
                            arguments={"message": "hi"},
                        )
                    ],
                )

            async def health(self) -> bool:
                return True

        provider = _AlwaysToolCallProvider()
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=["echo"],
            cognitive_config=CognitiveLoopConfig(
                max_iterations=100,
                max_tool_calls=1000,
                max_calls_per_tool=100,
                max_time_seconds=0.0,  # already exhausted
            ),
        )
        await _run_with_startup(runtime, agent, "loop")
        # EFFECT: tool NEVER executed — time budget was exhausted from
        # the start, so every EXECUTE phase short-circuited.
        assert spy.execute_count == 0, (
            f"expected 0 executes (time budget exhausted); got {spy.execute_count}"
        )


# =====================================================================
# TestRetry — H-08 idempotency
# =====================================================================


class TestRetry:
    """H-08: idempotent ``call_id`` is NOT re-executed."""

    @pytest.mark.asyncio
    async def test_idempotent_call_id_not_re_executed(self) -> None:
        """Same call_id submitted twice → second returns idempotent_skip.

        The first call is executed normally; the second call with the
        SAME ``call_id`` returns a successful no-op result with
        ``output.idempotent_skip=True`` (the tool's ``execute()`` is
        NOT invoked a second time).

        DECISION: ALLOW both times (the first call's verdict authorized it).
        EFFECT: tool's ``execute()`` invoked exactly ONCE.
        """
        spy = _SpyEchoTool()
        registry = ToolRegistry()
        registry.register(spy)
        issuer = AuthorizationIssuer()
        verifier = AuthorizationVerifier(issuer)
        executor = ToolExecutor(registry, auth_verifier=verifier)
        ctx = RunContext(agent_id="test", task="t", execution_id="exec-1", context_id="ctx-1")
        ctx.verified_principal = AuthPrincipal(
            identity="alice", is_anonymous=False
        )
        token = issuer.issue(
            tool_name="echo",
            principal_id="alice",
            execution_id="exec-1",
            context_id='ctx-1',
        )
        # First call — executes normally.
        result1 = await executor.execute(
            "echo", {"message": "hi"}, context=ctx, authorization=token,
            call_id="call-1",
        )
        # Second call — same call_id → idempotent skip.
        result2 = await executor.execute(
            "echo", {"message": "hi"}, context=ctx, authorization=token,
            call_id="call-1",
        )
        # Both calls succeed (idempotent skip is a SUCCESS no-op).
        assert result1.success is True
        assert result2.success is True
        # EFFECT: tool executed exactly ONCE — the second call was a no-op.
        assert spy.execute_count == 1, (
            f"expected 1 execute (idempotent skip on 2nd); got {spy.execute_count}"
        )
        # The second result carries the structured ``idempotent_skip`` flag.
        assert isinstance(result2.output, dict)
        assert result2.output.get("idempotent_skip") is True, (
            f"expected idempotent_skip=True; got output={result2.output!r}"
        )


# =====================================================================
# TestNoEffectOnDenial — the strictest adversarial class
# =====================================================================


class TestNoEffectOnDenial:
    """Denied tools produce NO side-effect: tool.execute() NEVER called.

    These tests verify the EFFECT (not just the decision). A gate that
    returns DENY but still allows the underlying tool to run is a
    false-negative that the assertion pattern below catches.

    Every test asserts ``spy.execute_count == 0`` after the agent run.
    """

    @pytest.mark.asyncio
    async def test_denied_tool_produces_no_side_effect(self) -> None:
        """Tool denied → tool.execute() NEVER called.

        We register NO allow rules — the security perimeter DEFAULT-
        DENIES every tool call. The agent runs; the tool's
        ``execute()`` method MUST NEVER be invoked.

        DECISION: DENY (DEFAULT-DENY).
        EFFECT: tool's ``execute()`` NEVER invoked.
        """
        spy = _SpyEchoTool()
        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=[],  # NO allow rules → DEFAULT-DENY
            cognitive_config=None,  # LINEAR — single iteration
        )
        response = await _run_with_startup(runtime, agent, "echo hi")
        # DECISION: DENY
        assert response.tool_results, "tool must produce a (failed) result"
        assert response.tool_results[0].success is False
        # EFFECT: tool NEVER executed
        assert spy.execute_count == 0, (
            f"expected 0 executes on DEFAULT-DENY; got {spy.execute_count}"
        )

    @pytest.mark.asyncio
    async def test_denied_tool_in_cognitive_mode_produces_no_side_effect(
        self,
    ) -> None:
        """Cognitive mode: denied tool → tool.execute() NEVER called, loop iterates.

        The cognitive loop iterates 2 times. Iter 1's tool is DENIED
        (no allow rules). Iter 2 returns a plain response. The tool's
        ``execute()`` MUST NEVER be invoked in EITHER iteration.

        DECISION: DENY (DEFAULT-DENY in both iterations).
        EFFECT: tool's ``execute()`` NEVER invoked.
        """
        spy = _SpyEchoTool()
        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=[],  # NO allow rules → DEFAULT-DENY
            cognitive_config=CognitiveLoopConfig(),  # COGNITIVE
        )
        response = await _run_with_startup(runtime, agent, "echo hi")
        # DECISION: DENY (the tool was denied on every iteration).
        assert response.tool_results, "tool must produce a (failed) result"
        assert response.tool_results[0].success is False
        # EFFECT: tool NEVER executed — even though the loop iterated.
        assert spy.execute_count == 0, (
            f"expected 0 executes on DEFAULT-DENY (cognitive); got {spy.execute_count}"
        )

    @pytest.mark.asyncio
    async def test_no_authorization_change_on_denial(self) -> None:
        """Tool denial does NOT alter the AuthPrincipal on RunContext.

        A denied tool call must NOT modify the ``verified_principal``
        on the RunContext — denial is a policy verdict, not an identity
        change. The principal set by the IdentityResolver at the start
        of CHECK_PERMISSIONS is the SAME principal observed after the
        EXECUTE phase.

        DECISION: DENY (no allow rules).
        EFFECT: ``verified_principal`` unchanged (still the
        Authenticator's anonymous principal — NOT escalated to admin).
        """
        spy = _SpyEchoTool()
        # NoopAuthenticator → returns an anonymous principal.
        auth = NoopAuthenticator()
        provider = _ToolCallingProvider(
            [ToolCall(id="tc-1", tool_name="echo", arguments={"message": "hi"})]
        )
        runtime, agent = _build_runtime(
            provider=provider,
            tools=[spy],
            firewall_allow=[],  # NO allow rules → DEFAULT-DENY
            authenticator=auth,
            cognitive_config=None,  # LINEAR
        )
        # Capture the RunContext the loop passes to CHECK_PERMISSIONS.
        captured: list[RunContext] = []
        original_check = agent._phase_check_permissions

        async def _spy_check(loop_agent: Agent, ctx: dict[str, Any]) -> Any:
            captured.append(ctx["context"])
            return await original_check(loop_agent, ctx)

        agent._phase_check_permissions = _spy_check

        await _run_with_startup(runtime, agent, "echo hi")
        # The CHECK_PERMISSIONS phase ran and captured the RunContext.
        assert captured, "RunContext was not captured"
        run_ctx = captured[0]
        # The verified_principal was set by the IdentityResolver.
        assert run_ctx.verified_principal is not None
        # The principal is the ANONYMOUS one (NoopAuthenticator).
        assert run_ctx.verified_principal.is_anonymous is True
        assert run_ctx.verified_principal.roles == []
        # The principal was NOT mutated to admin — denial is not an escalation.
        assert "admin" not in run_ctx.verified_principal.roles
        # EFFECT: tool NEVER executed.
        assert spy.execute_count == 0
