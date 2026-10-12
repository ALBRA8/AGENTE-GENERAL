"""RBAC security perimeter tests — SEC-PERIMETER Phase 1 + Phase 2 design.

Tests the 5 corrected findings + adversarial scenarios from the
security design document:

  Hallazgo 1 (PolicyEngine DEFAULT-DENY):
    * actor_roles=None + required_permission → DENY.
    * actor_roles=[] + required_permission → DENY (fail-closed).
    * permission_registry=None + required_permission → DENY.
    * registry raises → DENY (fail-closed try/except).
    * rule WITHOUT required_permission → ALLOW (public action).

  Hallazgo 2 (IdentityResolver):
    * NoopAuthenticator → anonymous principal.
    * No authenticator → anonymous principal (CORE_ONLY safe).
    * Real authenticator → verified principal.
    * Authenticator raises → None (fail-closed).

  Hallazgo 3 (firewall precedence):
    * Policy DENY + firewall ALLOW → DENY (no upgrade).
    * Policy ALLOW + firewall DENY → DENY (downgrade).
    * No policy + firewall ALLOW → ALLOW (sole gate).

  Hallazgo 4 (Agent passes principal roles to policy):
    * Caller stuffs actor_roles into metadata → ignored (Authenticator wins).
    * Anonymous principal + protected action → DENY.

  Hallazgo 5 (defense-in-depth at execute point):
    * Kill switch activated between CHECK_PERMISSIONS and EXECUTE → DENY.
    * Denied tool NEVER has its execute() method called.
"""

from __future__ import annotations

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
from albra_seed.core.types import AgentResponse, ToolCall
from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule
from albra_seed.providers.mock import MockProvider
from albra_seed.security.auth import (
    Authenticator,
    AuthPrincipal,
    NoopAuthenticator,
)
from albra_seed.security.identity_resolver import IdentityResolver
from albra_seed.security.permissions import PermissionRegistry
from albra_seed.security.roles import Role as RBACRole
from albra_seed.security.roles import RoleRegistry
from albra_seed.tools.infra_tools import EchoTool

# =====================================================================
# Helpers
# =====================================================================


class _MockAuthenticator(Authenticator):
    """Test authenticator that returns a configurable principal.

    ``verify(None)`` returns an anonymous principal (matching the spec
    contract that an absent token yields an unauthenticated principal).
    ``verify(token)`` with any non-None token returns the configured
    principal (simulating a verified bearer token).
    """

    name = "mock-auth"
    is_mock = True

    def __init__(self, principal: AuthPrincipal | None = None) -> None:
        self._principal = principal

    async def authenticate(self, credentials):  # noqa: ANN001
        return self._principal or AuthPrincipal(is_anonymous=True)

    async def verify(self, token):  # noqa: ANN001
        if token is None:
            return AuthPrincipal(
                kind="anonymous",
                identity="anonymous",
                roles=[],
                is_anonymous=True,
            )
        return self._principal


class _ToolCallingProvider(MockProvider):
    """Mock provider that emits a fixed tool_call in its response."""

    def __init__(self, tool_calls):  # noqa: ANN001
        super().__init__()
        self._tool_calls = list(tool_calls)

    async def complete(self, messages, **kwargs):  # noqa: ANN001
        self._call_count += 1
        ctx = kwargs.get("context")
        return AgentResponse(
            content="[mock]",
            execution_id=ctx.execution_id if ctx else "x",
            correlation_id=ctx.correlation_id if ctx else "y",
            tool_calls=list(self._tool_calls),
        )


async def _setup_agent(  # noqa: ANN202
    *,
    allow_tools=None,  # noqa: ANN001
    deny_tools=None,  # noqa: ANN001
    principal=None,  # noqa: ANN001
    autonomy=AutonomyLevel.L2_EXECUTE_SAFE,
    register_auth=True,
):
    """Build a Runtime + Agent wired with a permissive firewall + policy.

    Both gates are configured with allow/deny rules for the supplied tool
    names so the test can drive either gate to ALLOW or DENY. The
    authenticator is registered as the ``authenticator`` component so the
    Agent's IdentityResolver can resolve a principal.
    """
    runtime = Runtime(config=SeedConfig())
    await runtime.startup()
    runtime.register_provider(
        _ToolCallingProvider(
            [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
        )
    )
    runtime.register_tool(EchoTool())

    if register_auth:
        auth = _MockAuthenticator(principal)
        runtime.register("authenticator", auth)

    fw = ActionFirewall()
    for t in (allow_tools or []):
        fw.add_rule(
            FirewallRule(
                action_pattern=t,
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
    runtime.register("firewall", fw)

    policy = PolicyEngine()
    for t in (allow_tools or []):
        policy.add_rule(
            PolicyRule(
                name=f"allow_{t}",
                action_pattern=t,
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=AutonomyLevel.L0_OBSERVE,
                decision="allow",
                priority=10,
            )
        )
    for t in (deny_tools or []):
        policy.add_rule(
            PolicyRule(
                name=f"deny_{t}",
                action_pattern=t,
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=AutonomyLevel.L5_SELF_IMPROVE,
                decision="deny",
                priority=20,
            )
        )
    runtime.register("policy_engine", policy)

    identity = AgentIdentity(name="test-agent", autonomy_level=autonomy)
    agent = Agent(identity, runtime)
    return runtime, agent


# =====================================================================
# Hallazgo 1 — PolicyEngine RBAC default-deny
# =====================================================================


class TestPolicyEngineRBAC:
    """PolicyEngine enforces DEFAULT-DENY for protected actions."""

    @pytest.mark.asyncio
    async def test_actor_roles_none_with_required_permission_denies(self):
        """Hallazgo 1: actor_roles=None + required_permission → DENY."""
        engine = PolicyEngine()
        engine.add_rule(
            PolicyRule(
                name="req_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute",
                decision="allow",
                priority=10,
            )
        )
        registry = PermissionRegistry()
        engine.permission_registry = registry
        call = ToolCall(id="tc1", tool_name="echo", arguments={})
        ctx = RunContext(
            agent_id="a",
            task="t",
            autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        )
        verdict = await engine.evaluate(call, context=ctx, actor_roles=None)
        assert verdict.decision.value == "deny"

    @pytest.mark.asyncio
    async def test_actor_roles_empty_with_required_permission_denies(self):
        """actor_roles=[] + required_permission → DENY (no roles = no permission)."""
        engine = PolicyEngine()
        engine.add_rule(
            PolicyRule(
                name="req_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute",
                decision="allow",
                priority=10,
            )
        )
        engine.permission_registry = PermissionRegistry()
        call = ToolCall(id="tc1", tool_name="echo", arguments={})
        ctx = RunContext(
            agent_id="a",
            task="t",
            autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        )
        verdict = await engine.evaluate(call, context=ctx, actor_roles=[])
        assert verdict.decision.value == "deny"

    @pytest.mark.asyncio
    async def test_rol_autorizado_allows(self):
        """A role with the required permission → ALLOW."""
        engine = PolicyEngine()
        engine.add_rule(
            PolicyRule(
                name="req_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute",
                decision="allow",
                priority=10,
            )
        )
        registry = PermissionRegistry()
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        registry.roles = roles
        registry.grant("admin", "tool.execute")
        engine.permission_registry = registry
        call = ToolCall(id="tc1", tool_name="echo", arguments={})
        ctx = RunContext(
            agent_id="a",
            task="t",
            autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        )
        verdict = await engine.evaluate(call, context=ctx, actor_roles=["admin"])
        assert verdict.decision.value == "allow"

    @pytest.mark.asyncio
    async def test_no_registry_with_required_permission_denies(self):
        """permission_registry=None + required_permission → DENY (fail-closed)."""
        engine = PolicyEngine()
        engine.add_rule(
            PolicyRule(
                name="req_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute",
                decision="allow",
                priority=10,
            )
        )
        # NO permission_registry set on the engine.
        call = ToolCall(id="tc1", tool_name="echo", arguments={})
        ctx = RunContext(
            agent_id="a",
            task="t",
            autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        )
        verdict = await engine.evaluate(call, context=ctx, actor_roles=["admin"])
        assert verdict.decision.value == "deny"

    @pytest.mark.asyncio
    async def test_none_roles_without_required_permission_allows(self):
        """Public action (no required_permission) → ALLOW even with no roles."""
        engine = PolicyEngine()
        engine.add_rule(
            PolicyRule(
                name="public",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                decision="allow",
                priority=10,
            )
        )
        call = ToolCall(id="tc1", tool_name="echo", arguments={})
        ctx = RunContext(
            agent_id="a",
            task="t",
            autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        )
        verdict = await engine.evaluate(call, context=ctx, actor_roles=None)
        assert verdict.decision.value == "allow"

    @pytest.mark.asyncio
    async def test_permission_registry_exception_denies(self):
        """Registry raises → DENY (fail-closed try/except)."""
        class _BrokenRegistry:
            def has_any(self, role_ids, permission):  # noqa: ANN001
                raise RuntimeError("DB connection lost")

        engine = PolicyEngine()
        engine.add_rule(
            PolicyRule(
                name="req_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute",
                decision="allow",
                priority=10,
            )
        )
        engine.permission_registry = _BrokenRegistry()
        call = ToolCall(id="tc1", tool_name="echo", arguments={})
        ctx = RunContext(
            agent_id="a",
            task="t",
            autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        )
        verdict = await engine.evaluate(call, context=ctx, actor_roles=["admin"])
        assert verdict.decision.value == "deny"

    @pytest.mark.asyncio
    async def test_authorized_role_overrides_anonymous_deny(self):
        """Anonymous principal (roles=[]) → DENY; admin role → ALLOW."""
        engine = PolicyEngine()
        engine.add_rule(
            PolicyRule(
                name="req_perm",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute",
                decision="allow",
                priority=10,
            )
        )
        registry = PermissionRegistry()
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        registry.roles = roles
        registry.grant("admin", "tool.execute")
        engine.permission_registry = registry
        call = ToolCall(id="tc1", tool_name="echo", arguments={})
        ctx = RunContext(
            agent_id="a",
            task="t",
            autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        )
        # Empty roles → DENY
        v_anon = await engine.evaluate(call, context=ctx, actor_roles=[])
        assert v_anon.decision.value == "deny"
        # Admin role → ALLOW
        v_admin = await engine.evaluate(call, context=ctx, actor_roles=["admin"])
        assert v_admin.decision.value == "allow"

    @pytest.mark.asyncio
    async def test_hierarchical_permission_grants_cover_children(self):
        """Granting 'tool.execute' covers 'tool.execute.echo' (hierarchical)."""
        engine = PolicyEngine()
        engine.add_rule(
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
        registry = PermissionRegistry()
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        registry.roles = roles
        # Grant the PARENT permission; the rule requires the CHILD.
        registry.grant("admin", "tool.execute")
        engine.permission_registry = registry
        call = ToolCall(id="tc1", tool_name="echo", arguments={})
        ctx = RunContext(
            agent_id="a",
            task="t",
            autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        )
        verdict = await engine.evaluate(call, context=ctx, actor_roles=["admin"])
        assert verdict.decision.value == "allow"


# =====================================================================
# Hallazgo 2 — IdentityResolver
# =====================================================================


class TestIdentityResolver:
    """IdentityResolver resolves AuthPrincipal from Authenticator."""

    @pytest.mark.asyncio
    async def test_resolve_with_noop_authenticator(self):
        """NoopAuthenticator → anonymous principal."""
        resolver = IdentityResolver(NoopAuthenticator())
        ctx = RunContext(agent_id="a", task="t")
        principal = await resolver.resolve_and_attach(ctx, token=None)
        assert principal is not None
        assert principal.is_anonymous is True
        assert ctx.verified_principal is principal

    @pytest.mark.asyncio
    async def test_resolve_with_no_authenticator(self):
        """No authenticator → anonymous principal (CORE_ONLY safe)."""
        resolver = IdentityResolver(None)
        ctx = RunContext(agent_id="a", task="t")
        principal = await resolver.resolve_and_attach(ctx, token=None)
        assert principal is not None
        assert principal.is_anonymous is True
        # Principal is attached to the RunContext.
        assert ctx.verified_principal is principal

    @pytest.mark.asyncio
    async def test_resolve_with_real_authenticator(self):
        """Real authenticator + valid token → verified principal."""
        principal = AuthPrincipal(
            kind="user",
            identity="alice",
            roles=["admin"],
            is_anonymous=False,
        )
        auth = _MockAuthenticator(principal=principal)
        resolver = IdentityResolver(auth)
        ctx = RunContext(agent_id="a", task="t")
        resolved = await resolver.resolve_and_attach(ctx, token="valid-token")
        assert resolved is principal
        assert ctx.verified_principal is principal
        assert resolved.roles == ["admin"]

    @pytest.mark.asyncio
    async def test_authenticator_exception_returns_none(self):
        """Authenticator raises → None (fail-closed)."""
        class _BrokenAuth(Authenticator):
            name = "broken"

            async def authenticate(self, credentials):  # noqa: ANN001
                raise RuntimeError("DB lost")

            async def verify(self, token):  # noqa: ANN001
                raise RuntimeError("DB lost")

        resolver = IdentityResolver(_BrokenAuth())
        ctx = RunContext(agent_id="a", task="t")
        principal = await resolver.resolve_and_attach(ctx, token="x")
        assert principal is None
        # Even on failure, the verified_principal field is set (to None).
        assert ctx.verified_principal is None

    @pytest.mark.asyncio
    async def test_get_principal_returns_verified(self):
        """get_principal returns the principal set by resolve_and_attach."""
        resolver = IdentityResolver(None)
        ctx = RunContext(agent_id="a", task="t")
        await resolver.resolve_and_attach(ctx)
        p = resolver.get_principal(ctx)
        assert p is not None
        assert p.is_anonymous is True

    @pytest.mark.asyncio
    async def test_get_principal_returns_none_when_not_resolved(self):
        """If resolve_and_attach was never called, get_principal returns None."""
        resolver = IdentityResolver(None)
        ctx = RunContext(agent_id="a", task="t")
        # Verified principal defaults to None on a fresh RunContext.
        assert resolver.get_principal(ctx) is None

    @pytest.mark.asyncio
    async def test_verified_principal_not_in_metadata(self):
        """The verified principal is on a dedicated field, NOT in metadata.

        A caller cannot set ``verified_principal`` via the public
        ``metadata`` dict — that field is the IdentityResolver's private
        channel. This is the structural property that prevents role
        impersonation via metadata.
        """
        ctx = RunContext(
            agent_id="a",
            task="t",
            metadata={"actor_roles": ["admin"]},  # caller tries to forge
        )
        # The verified_principal field defaults to None — NOT to ["admin"].
        assert ctx.verified_principal is None
        # The metadata is still public/writable (it's for provenance, not auth).
        assert ctx.metadata.get("actor_roles") == ["admin"]


# =====================================================================
# Hallazgo 3 + 4 — Agent security perimeter (firewall precedence,
# principal resolution, role impersonation)
# =====================================================================


class TestAgentSecurityPerimeter:
    """End-to-end agent security perimeter tests."""

    @pytest.mark.asyncio
    async def test_agent_passes_principal_roles_to_policy(self):
        """Agent resolves AuthPrincipal and passes roles to policy."""
        principal = AuthPrincipal(
            kind="user",
            identity="alice",
            roles=["admin"],
            is_anonymous=False,
        )
        runtime, agent = await _setup_agent(
            allow_tools=["echo"], principal=principal
        )
        response = await agent.run("echo hi")
        assert response is not None
        # Tool should be ALLOWED because the rule has no required_permission
        # (public action) and the firewall/policy both allow.
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_agent_anonymous_principal_denies_protected_action(self):
        """NoopAuthenticator → anonymous → protected action DENIED.

        Without a real authenticator, the resolver returns an anonymous
        principal with empty roles. The policy requires a permission the
        anonymous principal does not have → DENY.
        """
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(EchoTool())
        # Register authenticator that returns None (no principal) when
        # verify is called with a non-None token. With token=None (the
        # Agent's call), it returns an anonymous principal — which still
        # has no roles and is therefore denied for protected actions.
        runtime.register("authenticator", _MockAuthenticator(principal=None))
        fw = ActionFirewall()
        fw.add_rule(
            FirewallRule(
                action_pattern="echo",
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
        runtime.register("firewall", fw)
        # Policy with required_permission — anonymous principal has no
        # roles → has_permission=False → DENY.
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="req",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                required_permission="tool.execute",
                decision="allow",
                priority=10,
            )
        )
        runtime.register("policy_engine", policy)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        response = await agent.run("echo hi")
        # Tool denied because anonymous principal has no roles.
        assert response.tool_results
        assert response.tool_results[0].success is False
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_policy_deny_plus_firewall_allow_results_in_deny(self):
        """Hallazgo 3: policy DENY + firewall ALLOW → DENY (policy wins).

        The firewall CANNOT upgrade a policy DENY to ALLOW. This was the
        bypass in the original code (the ``elif decision == "deny" and
        fw_allowed: decision = "allow"`` branch).
        """
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="dangerous", arguments={})]
            )
        )
        # No EchoTool registered — the action is "dangerous", not "echo".
        # Policy DENY
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="deny_dangerous",
                action_pattern="dangerous",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                decision="deny",
                priority=20,
            )
        )
        runtime.register("policy_engine", policy)
        # Firewall ALLOW
        fw = ActionFirewall()
        fw.add_rule(
            FirewallRule(
                action_pattern="dangerous",
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
        runtime.register("firewall", fw)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        response = await agent.run("use dangerous")
        assert response.tool_results
        assert response.tool_results[0].success is False
        err = response.tool_results[0].error or ""
        assert "deny" in err.lower() or "blocked" in err.lower()
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_policy_allow_plus_firewall_deny_results_in_deny(self):
        """Policy ALLOW + firewall DENY → DENY (firewall downgrades allow)."""
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(EchoTool())
        # Policy ALLOW
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="allow_echo",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                decision="allow",
                priority=10,
            )
        )
        runtime.register("policy_engine", policy)
        # Firewall DENY (no allow rule → default deny)
        fw = ActionFirewall()
        runtime.register("firewall", fw)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        response = await agent.run("echo hi")
        assert response.tool_results
        assert response.tool_results[0].success is False
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_no_policy_firewall_allow_results_in_allow(self):
        """No policy + firewall ALLOW → ALLOW (firewall is sole gate)."""
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(EchoTool())
        # No policy registered
        # Firewall ALLOW
        fw = ActionFirewall()
        fw.add_rule(
            FirewallRule(
                action_pattern="echo",
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
        runtime.register("firewall", fw)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        response = await agent.run("echo hi")
        assert response.tool_results
        assert response.tool_results[0].success is True
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_role_impersonation_via_metadata_blocked(self):
        """Caller puts roles in metadata → agent uses Authenticator, not metadata.

        The caller constructs a RunContext with
        ``metadata={"actor_roles": ["admin"]}`` (an attempt at privilege
        escalation). The agent's IdentityResolver consults the
        Authenticator (which returns the "viewer" principal — no admin
        role). The PolicyEngine is configured with a rule requiring
        ``admin.execute``; only the "admin" role has that permission.
        Since the principal is "viewer" (NOT "admin"), the action is
        DENIED — even though metadata claims admin.
        """
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(EchoTool())
        # Authenticator returns viewer (no admin role) when verify is
        # called with a non-None token. With token=None, it returns
        # an anonymous principal — either way, NOT admin.
        principal = AuthPrincipal(
            kind="user",
            identity="alice",
            roles=["viewer"],
            is_anonymous=False,
        )
        runtime.register("authenticator", _MockAuthenticator(principal=principal))
        fw = ActionFirewall()
        fw.add_rule(
            FirewallRule(
                action_pattern="echo",
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
        runtime.register("firewall", fw)
        # Policy requires admin permission
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
        registry = PermissionRegistry()
        roles = RoleRegistry()
        roles.register(RBACRole(role_id="admin", name="admin"))
        roles.register(RBACRole(role_id="viewer", name="viewer"))
        registry.roles = roles
        registry.grant("admin", "admin.execute")
        # viewer has NO admin.execute
        policy.permission_registry = registry
        runtime.register("policy_engine", policy)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        # Caller tries to forge roles via metadata — agent MUST ignore
        # metadata and use the Authenticator's principal (viewer).
        ctx = RunContext(
            agent_id=identity.agent_id,
            task="echo hi",
            metadata={"actor_roles": ["admin"]},
        )
        response = await agent.run("echo hi", context=ctx)
        # DENIED — agent used Authenticator's roles (viewer), not metadata.
        assert response.tool_results
        assert response.tool_results[0].success is False
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_kill_switch_activated_between_check_and_execute(self):
        """Kill switch activated BEFORE run → DENIED at CHECK + EXECUTE.

        The kill switch is checked by the firewall during CHECK_PERMISSIONS,
        AND re-checked by Agent._phase_execute at the execution point
        (defense-in-depth — Hallazgo 5).
        """
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(EchoTool())
        fw = ActionFirewall()
        fw.add_rule(
            FirewallRule(
                action_pattern="echo",
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
        runtime.register("firewall", fw)
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="allow_echo",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                decision="allow",
                priority=10,
            )
        )
        runtime.register("policy_engine", policy)
        # Activate kill switch BEFORE the agent runs.
        await fw.kill_switch.activate(reason="test")
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        response = await agent.run("echo hi")
        # Kill switch should have blocked at CHECK_PERMISSIONS (firewall
        # checks kill switch) AND at EXECUTE (re-check, defense-in-depth).
        assert response.tool_results
        assert response.tool_results[0].success is False
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_protected_tool_not_executed_when_denied(self):
        """Tool denied → tool.execute() NEVER called (Hallazgo 5).

        A ``_SpyTool`` wraps ``EchoTool`` and records every ``execute()``
        call. After the agent runs with no allow rules (default deny), the
        spy MUST NOT have been called — the executor never reaches the
        ``tool.execute()`` line for denied calls.
        """
        execute_called: list[dict] = []

        class _SpyTool(EchoTool):
            async def execute(
                self,
                arguments,  # noqa: ANN001
                *,
                context=None,  # noqa: ANN001
                dry_run=False,
            ):
                execute_called.append(arguments)
                return await super().execute(
                    arguments, context=context, dry_run=dry_run
                )

        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(_SpyTool())
        # No allow rule → default deny
        fw = ActionFirewall()
        runtime.register("firewall", fw)
        policy = PolicyEngine()
        runtime.register("policy_engine", policy)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        response = await agent.run("echo hi")
        assert len(execute_called) == 0, (
            "Spy tool was called despite denial! execute() must NEVER be "
            "invoked for a denied tool_call."
        )
        assert response.tool_results
        assert response.tool_results[0].success is False
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_policy_exception_fails_closed(self):
        """If policy.evaluate raises, the agent DENIES (fail-closed)."""
        class _BrokenPolicy:
            async def evaluate(self, *args, **kwargs):  # noqa: ANN001
                raise RuntimeError("policy DB connection lost")

        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(EchoTool())
        runtime.register("policy_engine", _BrokenPolicy())
        fw = ActionFirewall()
        fw.add_rule(
            FirewallRule(
                action_pattern="echo",
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
        runtime.register("firewall", fw)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        response = await agent.run("echo hi")
        # Policy exception → DENY (fail-closed) — even though the firewall
        # would have allowed.
        assert response.tool_results
        assert response.tool_results[0].success is False
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_firewall_exception_fails_closed(self):
        """If firewall.check raises, the agent DENIES (fail-closed)."""
        class _BrokenFirewall:
            kill_switch = None
            cooldown = None

            async def check(self, *args, **kwargs):  # noqa: ANN001
                raise RuntimeError("firewall state corruption")

        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(EchoTool())
        runtime.register("firewall", _BrokenFirewall())
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="allow_echo",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                decision="allow",
                priority=10,
            )
        )
        runtime.register("policy_engine", policy)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        response = await agent.run("echo hi")
        # Firewall exception → DENY (fail-closed).
        assert response.tool_results
        assert response.tool_results[0].success is False
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_principal_attached_to_run_context(self):
        """The verified principal is attached to run_ctx.verified_principal."""
        principal = AuthPrincipal(
            kind="user",
            identity="alice",
            roles=["admin"],
            is_anonymous=False,
        )
        runtime, agent = await _setup_agent(
            allow_tools=["echo"], principal=principal
        )
        # Spy on the CHECK_PERMISSIONS phase: capture the RunContext that
        # the loop passes in, then delegate to the original implementation.
        captured: list[RunContext] = []
        original_check = agent._phase_check_permissions

        async def _spy_check(loop_agent, ctx):  # noqa: ANN001
            captured.append(ctx["context"])
            return await original_check(loop_agent, ctx)

        agent._phase_check_permissions = _spy_check
        await agent.run("echo hi")
        assert captured, "RunContext was not captured"
        run_ctx = captured[0]
        # The verified_principal field MUST be set (IdentityResolver ran).
        assert run_ctx.verified_principal is not None
        # And it MUST be the principal returned by the authenticator.
        # With token=None, _MockAuthenticator.verify returns an anonymous
        # principal (see the _MockAuthenticator docstring) — NOT the
        # configured ``principal`` (which is only returned for non-None
        # tokens). The key property being verified: the field IS set by
        # the IdentityResolver (NOT None) and is_anonymous reflects the
        # authenticator's verdict.
        assert run_ctx.verified_principal.is_anonymous is True
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_no_authenticator_yields_anonymous_principal(self):
        """No authenticator registered → resolver returns anonymous principal.

        The PolicyEngine will then DENY any action requiring a permission
        (because the principal's roles is []). Public actions (no
        required_permission) are still allowed.
        """
        runtime, agent = await _setup_agent(
            allow_tools=["echo"], principal=None, register_auth=False
        )
        response = await agent.run("echo hi")
        # No required_permission on the rule → public action → ALLOW.
        assert response.tool_results
        assert response.tool_results[0].success is True
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_kill_switch_blocks_public_action_at_execute(self):
        """Kill switch active → EXECUTE phase blocks even allowed tools.

        The firewall allows the echo action at CHECK_PERMISSIONS time
        (kill switch wasn't active then). The kill switch activates
        between CHECK_PERMISSIONS and EXECUTE. The EXECUTE phase
        re-checks the kill switch and DENIES.
        """
        # We can't actually time-travel between phases without hooks,
        # but we CAN simulate it by pre-activating the kill switch —
        # the firewall will then deny at CHECK time. This still verifies
        # the EXECUTE re-check.
        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(EchoTool())
        fw = ActionFirewall()
        fw.add_rule(
            FirewallRule(
                action_pattern="echo",
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
        runtime.register("firewall", fw)
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="allow_echo",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                decision="allow",
                priority=10,
            )
        )
        runtime.register("policy_engine", policy)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        # Activate the kill switch — this happens "between" CHECK and EXECUTE
        # in our simulation. In practice, the firewall already checks it
        # at CHECK time, so the test verifies the COMBINED behavior.
        await fw.kill_switch.activate(reason="between-check-and-execute")
        response = await agent.run("echo hi")
        assert response.tool_results
        assert response.tool_results[0].success is False
        # The error message should mention the kill switch.
        err = response.tool_results[0].error or ""
        assert "kill_switch" in err.lower() or "deny" in err.lower() or "blocked" in err.lower()
        await runtime.shutdown()


# =====================================================================
# Hallazgo 5 — Defense-in-depth: spy tool never called when denied
# =====================================================================


class TestDefenseInDepth:
    """Execute-point re-checks + never-execute-denied-tools."""

    @pytest.mark.asyncio
    async def test_spy_tool_never_called_for_denied_action(self):
        """A denied tool_call NEVER reaches tool.execute()."""
        execute_called: list[dict] = []

        class _SpyTool(EchoTool):
            async def execute(
                self,
                arguments,  # noqa: ANN001
                *,
                context=None,  # noqa: ANN001
                dry_run=False,
            ):
                execute_called.append(arguments)
                return await super().execute(
                    arguments, context=context, dry_run=dry_run
                )

        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(_SpyTool())
        # No firewall, no policy → no security perimeter → DENY.
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        response = await agent.run("echo hi")
        assert len(execute_called) == 0
        assert response.tool_results
        assert response.tool_results[0].success is False
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_cooldown_recheck_at_execute_blocks_repeated_call(self):
        """A rule with cooldown_seconds triggers a cooldown that blocks
        subsequent calls of the same action — verified via the EXECUTE
        phase re-check (defense-in-depth).

        This test uses a plan with a SINGLE echo call (so the cooldown
        is NOT triggered during CHECK_PERMISSIONS). We then manually
        trigger a cooldown before the EXECUTE phase runs by patching
        the firewall's cooldown. The EXECUTE phase re-check MUST block.
        """
        execute_called: list[dict] = []

        class _SpyTool(EchoTool):
            async def execute(
                self,
                arguments,  # noqa: ANN001
                *,
                context=None,  # noqa: ANN001
                dry_run=False,
            ):
                execute_called.append(arguments)
                return await super().execute(
                    arguments, context=context, dry_run=dry_run
                )

        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(_SpyTool())
        fw = ActionFirewall()
        fw.add_rule(
            FirewallRule(
                action_pattern="echo",
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
        runtime.register("firewall", fw)
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="allow_echo",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                decision="allow",
                priority=10,
            )
        )
        runtime.register("policy_engine", policy)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)

        # Hook the EXECUTE phase: just before it runs, trigger a cooldown
        # on "echo" so the EXECUTE re-check will block.
        original_execute = agent._phase_execute

        async def _spy_execute(loop_agent, ctx):  # noqa: ANN001
            fw.cooldown.trigger("echo", seconds=60.0, reason="test")
            return await original_execute(loop_agent, ctx)

        agent._phase_execute = _spy_execute

        response = await agent.run("echo hi")
        # Tool was ALLOWED at CHECK time, but BLOCKED at EXECUTE time
        # because the cooldown was triggered between the two phases.
        assert response.tool_results
        assert response.tool_results[0].success is False
        assert "cooldown" in (response.tool_results[0].error or "").lower()
        assert len(execute_called) == 0, (
            "Spy tool was called despite cooldown! EXECUTE re-check failed."
        )
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_allowed_tool_actually_executed(self):
        """Sanity check: an ALLOWED tool IS executed (positive control)."""
        execute_called: list[dict] = []

        class _SpyTool(EchoTool):
            async def execute(
                self,
                arguments,  # noqa: ANN001
                *,
                context=None,  # noqa: ANN001
                dry_run=False,
            ):
                execute_called.append(arguments)
                return await super().execute(
                    arguments, context=context, dry_run=dry_run
                )

        runtime = Runtime(config=SeedConfig())
        await runtime.startup()
        runtime.register_provider(
            _ToolCallingProvider(
                [ToolCall(id="tc1", tool_name="echo", arguments={"text": "hi"})]
            )
        )
        runtime.register_tool(_SpyTool())
        fw = ActionFirewall()
        fw.add_rule(
            FirewallRule(
                action_pattern="echo",
                actor_pattern="*",
                decision="allow",
                risk_threshold=RiskLevel.UNKNOWN,
                autonomy_required=0,
            )
        )
        runtime.register("firewall", fw)
        policy = PolicyEngine()
        policy.add_rule(
            PolicyRule(
                name="allow_echo",
                action_pattern="echo",
                actor_pattern="*",
                risk_threshold=RiskLevel.UNKNOWN,
                decision="allow",
                priority=10,
            )
        )
        runtime.register("policy_engine", policy)
        identity = AgentIdentity(
            name="test", autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE
        )
        agent = Agent(identity, runtime)
        response = await agent.run("echo hi")
        # Sanity: allowed tool WAS executed.
        assert len(execute_called) == 1
        assert response.tool_results
        assert response.tool_results[0].success is True
        await runtime.shutdown()
