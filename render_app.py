from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

from albra_seed import Runtime, SeedConfig, create_app
from albra_seed.approvals.engine import ApprovalEngine
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import PolicyDecision, RiskLevel
from albra_seed.firewall.firewall import ActionFirewall, FirewallRule
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule, PolicyRuleSet
from albra_seed.runtime.agent import Agent
from albra_seed.runtime.lifecycle import LifecyclePhase
from albra_seed.security.auth import AuthPrincipal
from albra_seed.security.permissions import PermissionRegistry
from albra_seed.security.roles import Role, RoleRegistry

env_file = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_file)


class DevAuthenticator:
    """Authenticates Bearer tokens for local development and admin operations."""

    async def verify(self, token: str | None) -> AuthPrincipal | None:
        if not token or token != "albra-dev-token":
            return None
        return AuthPrincipal(
            kind="user",
            identity="admin",
            roles=["reviewer"],
            auth_method="bearer",
            is_anonymous=False,
        )


runtime = Runtime(config=SeedConfig())

# Security perimeter: Firewall
fw = ActionFirewall()
fw.add_rule(
    FirewallRule(
        action_pattern="*",
        actor_pattern="*",
        decision="allow",
        risk_threshold=RiskLevel.UNKNOWN,
        autonomy_required=0,
    )
)
runtime.register("firewall", fw)

# HITL Approval Engine
approval_engine = ApprovalEngine()
runtime.register("approval_engine", approval_engine)

# RBAC Registries
role_reg = RoleRegistry()
role_reg.register(Role(role_id="reviewer", name="Reviewer"))
perm_reg = PermissionRegistry(roles=role_reg)
perm_reg.grant("reviewer", "approval:list")
perm_reg.grant("reviewer", "approval:decide")
perm_reg.grant("reviewer", "approval:resume")
perm_reg.grant("reviewer", "execution:run")
perm_reg.grant("reviewer", "audio:transcribe")
runtime.register("permission_registry", perm_reg)

# Policy Engine
rules = PolicyRuleSet()
for act in ["approval:list", "approval:decide", "approval:resume", "execution:run", "audio:transcribe"]:
    rules.add(
        PolicyRule(
            name=f"allow-{act}",
            action_pattern=act,
            actor_pattern="*",
            risk_threshold=RiskLevel.UNKNOWN,
            required_permission=act,
            decision=PolicyDecision.ALLOW,
        )
    )
policy_engine = PolicyEngine(rule_set=rules, permission_registry=perm_reg)
runtime.register("policy_engine", policy_engine)

# Authenticator
runtime.register("authenticator", DevAuthenticator())

# Providers (NVIDIA / OpenAI integration)
openai_key = os.environ.get("OPENAI_API_KEY")
configured_providers: list[str] = []
if openai_key:
    from albra_seed.providers.openai_provider import OpenAIProvider

    base_url = os.environ.get("OPENAI_BASE_URL", "https://integrate.api.nvidia.com/v1")
    model = os.environ.get("ALBRA_DEFAULT_MODEL", "z-ai/glm-5.3-flash")
    provider = OpenAIProvider(api_key=openai_key, base_url=base_url, model=model)
    runtime.register_provider(provider)
    configured_providers.append("openai")

# Default Agent
identity = AgentIdentity(
    name="albra-agent",
    version="1.0.0",
    specialization="general",
    providers=configured_providers,
)
agent = Agent(identity=identity, runtime=runtime)


@asynccontextmanager
async def lifespan(app_instance: any) -> AsyncGenerator[None, None]:
    if runtime.lifecycle.phase == LifecyclePhase.UNINITIALIZED:
        await runtime.startup()
    try:
        yield
    finally:
        if runtime.lifecycle.phase != LifecyclePhase.STOPPED:
            await runtime.shutdown()


app = create_app(runtime, agents={identity.agent_id: agent})
app.router.lifespan_context = lifespan

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
