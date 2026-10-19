from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

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
from albra_seed.security.auth import AuthPrincipal, TokenAuthenticator
from albra_seed.security.permissions import PermissionRegistry
from albra_seed.security.roles import Role, RoleRegistry

env_file = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_file)


# ---------------------------------------------------------------------------
# Environment detection — production deployments must NOT silently fall
# back to insecure dev credentials. Two independent signals are used:
#
#   * APP_ENV: explicit environment tag ("production" | "dev" | "staging" | ...)
#   * PYTHONPATH or process env indicating a containerized/Cloud Run/Render
#     deployment (presence of RENDER or DYNO env vars).
#
# A non-dev environment requires the operator to provide a real
# authenticator via ALBRA_AUTHENTICATOR_MODULE / ALBRA_AUTHENTICATOR_CLASS
# (resolved at runtime through runtime.register()). If neither is wired
# and we are in production, the app refuses to register DevAuthenticator
# and every authenticated request will fail with 503 — that is the
# safe-by-default posture required by the security review.
# ---------------------------------------------------------------------------
def _is_production_env() -> bool:
    if os.environ.get("APP_ENV", "").lower() in {"production", "prod"}:
        return True
    if os.environ.get("RENDER") or os.environ.get("DYNO"):
        return True
    return False


PRODUCTION_ENV = _is_production_env()
DEV_AUTH_TOKEN = os.environ.get("ALBRA_DEV_TOKEN", "albra-dev-token")


class DevAuthenticator:
    """Authenticates Bearer tokens for LOCAL DEVELOPMENT ONLY.

    In production deployments this authenticator is NOT registered — the
    runtime falls back to "no authenticator", which causes every
    authenticated endpoint to return 503 (the safe-by-default posture).
    The dev token value is configurable via ALBRA_DEV_TOKEN, but it is
    never accepted when APP_ENV=production or when running on a known
    managed platform (RENDER / DYNO env vars present).
    """

    async def verify(self, token: str | None) -> AuthPrincipal | None:
        if PRODUCTION_ENV:
            # Hard fail-closed: dev token must NEVER work in production.
            return None
        if not token or token != DEV_AUTH_TOKEN:
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

# HITL Approval Engine — durable SQLite storage for production and recovery
from albra_seed.approvals.sqlite_store import SQLiteApprovalStore

approval_db_path = os.environ.get("APPROVAL_DB_PATH")
if not approval_db_path:
    data_dir = os.environ.get("DATA_DIR", os.path.expanduser("~/.local/share/albra"))
    approval_db_path = os.path.join(data_dir, "approvals.db")

try:
    approval_store = SQLiteApprovalStore(approval_db_path)
    approval_engine = ApprovalEngine(store=approval_store)
except Exception as exc:
    if PRODUCTION_ENV:
        raise RuntimeError(
            f"Failed to initialize durable SQLiteApprovalStore at {approval_db_path}: {exc}"
        ) from exc
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
perm_reg.grant("reviewer", "execution:read")
perm_reg.grant("reviewer", "audio:transcribe")
perm_reg.grant("reviewer", "diagnostics:read")
runtime.register("permission_registry", perm_reg)

# Policy Engine
rules = PolicyRuleSet()
for act in [
    "approval:list",
    "approval:decide",
    "approval:resume",
    "execution:run",
    "execution:read",
    "audio:transcribe",
    "diagnostics:read",
]:
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


def _resolve_auth_token() -> str:
    """Resolve authentication token with strict conflict detection.

    If both ALBRA_API_KEY and ALBRA_AUTH_TOKEN are set, they must match exactly;
    any contradiction is rejected to prevent ambiguous or weak credential overrides.
    """
    key = os.environ.get("ALBRA_API_KEY", "").strip()
    alt = os.environ.get("ALBRA_AUTH_TOKEN", "").strip()
    if key and alt and key != alt:
        raise RuntimeError(
            "Conflicting authentication configuration: ALBRA_API_KEY and ALBRA_AUTH_TOKEN "
            "are both set with different values. Remove ALBRA_AUTH_TOKEN or ensure they match."
        )
    return key or alt


# Authenticator — production-safe wiring.
# In production (PRODUCTION_ENV=True), a valid ALBRA_API_KEY (or ALBRA_AUTH_TOKEN)
# of at least 32 characters must be configured.
# If missing, empty, or insecure (< 32 chars), the authenticator slot remains empty,
# and lifespan will explicitly abort server startup with RuntimeError.
# In dev mode, TokenAuthenticator is used if a >= 32 char token is configured,
# otherwise DevAuthenticator falls back to local dev credentials.
prod_auth_token = _resolve_auth_token()

if PRODUCTION_ENV:
    if len(prod_auth_token) >= 32:
        runtime.register(
            "authenticator",
            TokenAuthenticator(prod_auth_token, identity="admin", roles=["reviewer"]),
        )
    else:
        # In production without valid token, leave slot empty (lifespan aborts on startup).
        pass
else:
    if len(prod_auth_token) >= 32:
        runtime.register(
            "authenticator",
            TokenAuthenticator(prod_auth_token, identity="admin", roles=["reviewer"]),
        )
    else:
        runtime.register("authenticator", DevAuthenticator())

# Providers (Groq, NVIDIA NIM, OpenAI)
configured_providers: list[str] = []
from albra_seed.providers.openai_provider import OpenAIProvider
from albra_seed.providers.registry import ProviderRegistry
from albra_seed.providers.router import ProviderRouter

provider_reg = ProviderRegistry()

groq_key = os.environ.get("GROQ_API_KEY")
if groq_key:
    groq_provider = OpenAIProvider(
        api_key=groq_key,
        base_url="https://api.groq.com/openai/v1",
        model=os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"),
    )
    groq_provider.name = "groq"
    runtime.register_provider(groq_provider)
    provider_reg.register(groq_provider)
    configured_providers.append("groq")

nvidia_key = os.environ.get("NVIDIA_API_KEY")
if nvidia_key:
    nvidia_provider = OpenAIProvider(
        api_key=nvidia_key,
        base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        model=os.environ.get("NVIDIA_MODEL", "z-ai/glm-5.3-flash"),
    )
    nvidia_provider.name = "nvidia"
    runtime.register_provider(nvidia_provider)
    provider_reg.register(nvidia_provider)
    configured_providers.append("nvidia")

openai_key = os.environ.get("OPENAI_API_KEY")
if openai_key:
    base_url = os.environ.get("OPENAI_BASE_URL")
    model = os.environ.get("ALBRA_DEFAULT_MODEL", "gpt-4o-mini")
    openai_provider = OpenAIProvider(api_key=openai_key, base_url=base_url, model=model)
    openai_provider.name = "openai"
    runtime.register_provider(openai_provider)
    provider_reg.register(openai_provider)
    configured_providers.append("openai")

if configured_providers:
    router = ProviderRouter(
        registry=provider_reg,
        default_provider=configured_providers[0],
        fallback_chain=configured_providers,
    )
    runtime.register("provider_router", router)

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
    if PRODUCTION_ENV:
        token = _resolve_auth_token()
        if not token:
            raise RuntimeError(
                "CRITICAL: Production startup aborted. ALBRA_API_KEY (or ALBRA_AUTH_TOKEN) "
                "must be configured in production. The server refuses to start unauthenticated."
            )
        if len(token) < 32:
            raise RuntimeError(
                f"CRITICAL: Production startup aborted. ALBRA_API_KEY must be at least 32 "
                f"characters in production (got {len(token)}). Weak credentials are rejected."
            )
        auth = runtime.get_component("authenticator")
        if auth is None or getattr(auth, "is_mock", True):
            raise RuntimeError(
                "CRITICAL: Production startup aborted. A secure non-mock authenticator must be "
                "registered in runtime before serving traffic."
            )
    if runtime.lifecycle.phase == LifecyclePhase.STOPPED:
        runtime.lifecycle.force(LifecyclePhase.UNINITIALIZED)
    if runtime.lifecycle.phase == LifecyclePhase.UNINITIALIZED:
        await runtime.startup()
    try:
        yield
    finally:
        if runtime.lifecycle.phase != LifecyclePhase.STOPPED:
            await runtime.shutdown()


app = create_app(runtime, agents={identity.agent_id: agent})
app.router.lifespan_context = lifespan


# ---------------------------------------------------------------------------
# CORS configuration — production-safe.
#
# Previous setup combined `allow_origins=["*"]` with `allow_credentials=True`,
# which is rejected by the CORS spec and by Starlette's middleware: a
# credentialed request cannot be answered with a wildcard Origin. We
# instead resolve allowed origins from ALBRA_CORS_ORIGINS (comma-separated)
# and only enable credentials when at least one explicit origin is set.
#
# In production (PRODUCTION_ENV=True) the app refuses to start if no
# explicit origins are configured — wildcard + credentials is unsafe and
# wildcard alone would still let any third-party page issue authenticated
# requests against the API.
# ---------------------------------------------------------------------------
def _resolve_cors_origins() -> list[str]:
    raw = os.environ.get("ALBRA_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


_cors_origins = _resolve_cors_origins()

if PRODUCTION_ENV and not _cors_origins:
    # Fail-safe startup: refuse to serve with permissive CORS in production.
    raise RuntimeError(
        "ALBRA_CORS_ORIGINS must be configured in production "
        "(comma-separated list of allowed origins). Wildcard CORS is not permitted."
    )

# Dev fallback: explicit localhost origins so the dashboard on :3000
# can call the API on :9000 without being blocked. Still NOT a wildcard.
if not _cors_origins and not PRODUCTION_ENV:
    _cors_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:9000",
        "http://127.0.0.1:9000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=bool(_cors_origins),  # only true when explicit origins
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)
