"""The ALBRA Runtime.

The Runtime is the top-level container for an ALBRA agent process. It owns:

  * the SeedConfig (immutable after startup),
  * the LifecycleManager (state machine + history),
  * the component registry (Tools, LLMProviders, MemoryStores, Skills,
    Plugins, Guardrails, and named infra components like "firewall",
    "provider_router", "policy_engine", "approval_engine" — all registered
    at runtime by specialization layers),
  * the active session map (bounded by `max_concurrent_sessions`),
  * per-phase event hooks.

Other subagents build the actual security, provider, memory, ... modules.
They register themselves with the Runtime; the Runtime never imports them
directly. This keeps the seed decoupled and testable in CORE_ONLY mode.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Awaitable

import structlog

from albra_seed.core.base import (
    Guardrail,
    LLMProvider,
    MemoryStore,
    Plugin,
    Skill,
    Tool,
)
from albra_seed.core.config import SeedConfig
from albra_seed.core.types import HealthState, Session, utc_now
from albra_seed.runtime.lifecycle import (
    LifecycleEvent,
    LifecycleManager,
    LifecyclePhase,
)
from albra_seed.runtime.loop import EventHook
from albra_seed.runtime.state import AgentState

log = structlog.get_logger("albra.runtime")


# Canonical names of components other subagents register. The Runtime uses
# these as keys; missing components are reported as NOT_CONFIGURED rather
# than raising — CORE_ONLY mode requires graceful degradation.
COMPONENT_FIREWALL = "firewall"
COMPONENT_POLICY_ENGINE = "policy_engine"
COMPONENT_APPROVAL_ENGINE = "approval_engine"
COMPONENT_PROVIDER_ROUTER = "provider_router"
COMPONENT_TOOL_REGISTRY = "tool_registry"
COMPONENT_MEMORY_STORE = "memory_store"
COMPONENT_EVIDENCE_COLLECTOR = "evidence_collector"
COMPONENT_AUDIT_LOGGER = "audit_logger"

# Components considered required for readiness. Empty list means READY is
# always true once INITIALIZED — used in CORE_ONLY mode.
REQUIRED_FOR_READINESS: tuple[str, ...] = (COMPONENT_PROVIDER_ROUTER,)


class Runtime:
    """Top-level runtime container. One per process (typically)."""

    def __init__(self, config: SeedConfig | None = None) -> None:
        self.config: SeedConfig = config or SeedConfig()
        self.lifecycle: LifecycleManager = LifecycleManager()
        self._components: dict[str, Any] = {}
        self._tools: dict[str, Tool] = {}
        self._providers: dict[str, LLMProvider] = {}
        self._memory_stores: dict[str, MemoryStore] = {}
        self._skills: dict[str, Skill] = {}
        self._plugins: dict[str, Plugin] = {}
        self._guardrails: list[Guardrail] = []
        self._sessions: dict[str, Session] = {}
        self._sessions_lock = asyncio.Lock()
        self._agent_states: dict[str, AgentState] = {}
        self._phase_hooks: list[EventHook] = []
        self._started_at: datetime | None = None

    # =====================================================================
    # LIFECYCLE
    # =====================================================================

    async def startup(self) -> None:
        """Move UNINITIALIZED → INITIALIZING → READY.

        Startup is intentionally minimal: it transitions the lifecycle and
        logs. Component registration happens via `register(...)` before or
        after startup; the readiness check (`readiness()`) reports the truth.
        """
        if self.lifecycle.phase != LifecyclePhase.UNINITIALIZED:
            raise RuntimeError(
                f"cannot startup from phase={self.lifecycle.phase.value}"
            )
        self.lifecycle.transition(
            LifecyclePhase.INITIALIZING, reason="startup requested"
        )
        self._started_at = utc_now()
        log.info(
            "runtime.startup",
            core_only=self.config.is_core_only(),
            components=list(self._components.keys()),
        )
        self.lifecycle.transition(
            LifecyclePhase.READY, reason="startup complete"
        )

    async def shutdown(self) -> None:
        """Move any phase → SHUTTING_DOWN → STOPPED. Idempotent."""
        if self.lifecycle.phase in {
            LifecyclePhase.STOPPED,
            LifecyclePhase.SHUTTING_DOWN,
        }:
            return
        if self.lifecycle.can_transition(LifecyclePhase.SHUTTING_DOWN):
            self.lifecycle.transition(
                LifecyclePhase.SHUTTING_DOWN, reason="shutdown requested"
            )
        else:
            # Forced path from ERROR or RUNNING.
            self.lifecycle.force(
                LifecyclePhase.SHUTTING_DOWN, reason="forced shutdown"
            )
        # Drop sessions cleanly.
        async with self._sessions_lock:
            self._sessions.clear()
        self.lifecycle.transition(
            LifecyclePhase.STOPPED, reason="shutdown complete"
        )
        log.info("runtime.shutdown", phase=self.lifecycle.phase.value)

    # =====================================================================
    # HEALTH / READINESS
    # =====================================================================

    def health(self) -> dict[str, Any]:
        """Return {status, components, sessions, uptime}.

        status:
            healthy   — READY/RUNNING and all required components present
            degraded  — running but some optional components missing
            error     — in ERROR phase
        """
        phase = self.lifecycle.phase
        if phase == LifecyclePhase.ERROR:
            status = "error"
        elif phase in {LifecyclePhase.STOPPED, LifecyclePhase.UNINITIALIZED}:
            status = "degraded"
        else:
            missing_required = [
                name
                for name in REQUIRED_FOR_READINESS
                if name not in self._components
            ]
            status = "healthy" if not missing_required else "degraded"

        components: dict[str, str] = {}
        for name in [
            COMPONENT_FIREWALL,
            COMPONENT_POLICY_ENGINE,
            COMPONENT_APPROVAL_ENGINE,
            COMPONENT_PROVIDER_ROUTER,
            COMPONENT_TOOL_REGISTRY,
            COMPONENT_MEMORY_STORE,
            COMPONENT_EVIDENCE_COLLECTOR,
            COMPONENT_AUDIT_LOGGER,
        ]:
            components[name] = (
                "configured" if name in self._components else "not_configured"
            )
        components["tools"] = f"{len(self._tools)}"
        components["providers"] = f"{len(self._providers)}"
        components["guardrails"] = f"{len(self._guardrails)}"

        return {
            "status": status,
            "phase": phase.value,
            "core_only": self.config.is_core_only(),
            "components": components,
            "sessions": len(self._sessions),
            "uptime_seconds": (
                (utc_now() - self._started_at).total_seconds()
                if self._started_at
                else 0.0
            ),
        }

    def readiness(self) -> bool:
        """True only when READY or RUNNING and required components present."""
        if self.lifecycle.phase not in {
            LifecyclePhase.READY,
            LifecyclePhase.RUNNING,
        }:
            return False
        return all(
            name in self._components for name in REQUIRED_FOR_READINESS
        )

    # =====================================================================
    # COMPONENT REGISTRY
    # =====================================================================

    def register(self, name: str, component: Any) -> None:
        """Register a named component (firewall, provider_router, ...)."""
        self._components[name] = component
        log.debug("runtime.register", name=name, type=type(component).__name__)

    def get_component(self, name: str, default: Any = None) -> Any:
        """Get a named component or `default` if not registered."""
        return self._components.get(name, default)

    def require_component(self, name: str) -> Any:
        """Get a named component or raise if missing."""
        if name not in self._components:
            raise KeyError(f"component not registered: {name}")
        return self._components[name]

    # Typed registrars ----------------------------------------------------

    def register_tool(self, tool: Tool) -> None:
        name = tool.contract.name
        self._tools[name] = tool

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def register_provider(self, provider: LLMProvider) -> None:
        self._providers[provider.name] = provider

    def get_provider(self, name: str) -> LLMProvider | None:
        return self._providers.get(name)

    def register_memory_store(self, domain: str, store: MemoryStore) -> None:
        self._memory_stores[domain] = store

    def get_memory_store(self, domain: str = "default") -> MemoryStore | None:
        return self._memory_stores.get(domain)

    def register_skill(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get_skill(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def register_plugin(self, plugin: Plugin) -> None:
        self._plugins[plugin.name] = plugin

    def register_guardrail(self, guardrail: Guardrail) -> None:
        self._guardrails.append(guardrail)

    @property
    def guardrails(self) -> list[Guardrail]:
        return list(self._guardrails)

    # =====================================================================
    # SESSIONS
    # =====================================================================

    async def register_session(self, session: Session) -> Session:
        """Store a session. Raises if max_concurrent_sessions exceeded."""
        async with self._sessions_lock:
            if (
                session.session_id not in self._sessions
                and len(self._sessions) >= self.config.max_concurrent_sessions
            ):
                raise RuntimeError(
                    f"max_concurrent_sessions="
                    f"{self.config.max_concurrent_sessions} exceeded"
                )
            self._sessions[session.session_id] = session
            return session

    async def get_session(self, session_id: str) -> Session | None:
        async with self._sessions_lock:
            return self._sessions.get(session_id)

    async def remove_session(self, session_id: str) -> bool:
        async with self._sessions_lock:
            return self._sessions.pop(session_id, None) is not None

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)

    # =====================================================================
    # AGENT STATE + HOOKS
    # =====================================================================

    def register_agent_state(self, state: AgentState) -> None:
        self._agent_states[state.agent_id] = state

    def get_agent_state(self, agent_id: str) -> AgentState | None:
        return self._agent_states.get(agent_id)

    def add_phase_hook(self, hook: EventHook) -> None:
        """Register a hook fired for every PhaseEvent from any AgentLoop."""
        self._phase_hooks.append(hook)

    @property
    def phase_hooks(self) -> list[EventHook]:
        return list(self._phase_hooks)


__all__ = [
    "Runtime",
    "COMPONENT_FIREWALL",
    "COMPONENT_POLICY_ENGINE",
    "COMPONENT_APPROVAL_ENGINE",
    "COMPONENT_PROVIDER_ROUTER",
    "COMPONENT_TOOL_REGISTRY",
    "COMPONENT_MEMORY_STORE",
    "COMPONENT_EVIDENCE_COLLECTOR",
    "COMPONENT_AUDIT_LOGGER",
    "REQUIRED_FOR_READINESS",
]
