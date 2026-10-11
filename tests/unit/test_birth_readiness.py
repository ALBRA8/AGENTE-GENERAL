"""Birth / Readiness contract tests (GAP-5).

These tests verify the BIRTH CONTRACT introduced in GAP-5:

* `Runtime.startup()` auto-registers a `provider_router` (with the
  always-on Mock) so CORE_ONLY birth works out of the box.
* Caller's explicit `provider_router` registration is NEVER overwritten.
* `Runtime.readiness()` returns True immediately after `startup()` in
  CORE_ONLY mode.
* `Runtime.birth()` returns a snapshot dict that proves the seed can
  execute the universal agent loop end-to-end using only Mock, with
  no external providers.

All tests run in CORE_ONLY mode (default `SeedConfig()` — no API keys).
No network. No external services.
"""

from __future__ import annotations

import structlog
import structlog.testing

from albra_seed.core.config import SeedConfig
from albra_seed.core.identity import AgentIdentity
from albra_seed.providers.router import ProviderRouter
from albra_seed.runtime import Agent, Runtime
from albra_seed.runtime.runtime import COMPONENT_PROVIDER_ROUTER

# =====================================================================
# 1. startup() auto-registers provider_router
# =====================================================================


async def test_runtime_auto_registers_provider_router_on_startup() -> None:
    """A fresh Runtime with no manual registration must have a
    provider_router after startup() — CORE_ONLY birth contract."""
    runtime = Runtime(SeedConfig())
    # Sanity: nothing registered before startup.
    assert runtime.get_component(COMPONENT_PROVIDER_ROUTER) is None

    await runtime.startup()

    router = runtime.get_component(COMPONENT_PROVIDER_ROUTER)
    assert router is not None, "provider_router must be auto-registered"
    assert isinstance(router, ProviderRouter)
    # The auto-created router carries a registry with the always-on Mock.
    assert router.registry.get("mock") is not None
    assert "mock" in router.registry.list_all()
    # And the bootstrap source is recorded.
    assert runtime._bootstrap_source == "auto"  # noqa: SLF001
    await runtime.shutdown()


# =====================================================================
# 2. Caller's explicit provider_router registration always wins
# =====================================================================


async def test_runtime_does_not_overwrite_caller_provider_router() -> None:
    """If the caller registers provider_router BEFORE startup, the
    auto-bootstrap must NOT overwrite it. Caller wins."""
    runtime = Runtime(SeedConfig())
    custom_router = ProviderRouter(default_provider="mock")
    # Mark the custom router so we can detect if it survives startup.
    custom_router._sentinel = "caller-owns-me"  # type: ignore[attr-defined]  # noqa: SLF001
    runtime.register(COMPONENT_PROVIDER_ROUTER, custom_router)

    await runtime.startup()

    router_after = runtime.get_component(COMPONENT_PROVIDER_ROUTER)
    assert router_after is custom_router, "caller's router must survive startup"
    assert getattr(router_after, "_sentinel", None) == "caller-owns-me"
    assert runtime._bootstrap_source == "caller"  # noqa: SLF001
    await runtime.shutdown()


# =====================================================================
# 3. readiness() is True in CORE_ONLY mode
# =====================================================================


async def test_runtime_readiness_true_in_core_only() -> None:
    """A SeedConfig() with no API keys + startup() → readiness() True.

    This is the heart of the CORE_ONLY birth contract: NO manual
    registration of any component is required for the seed to be ready.
    """
    config = SeedConfig()
    assert config.is_core_only() is True, "default SeedConfig must be CORE_ONLY"
    # No API keys set on any external provider.
    assert not any(
        [
            config.openai_api_key,
            config.anthropic_api_key,
            config.groq_api_key,
            config.zai_api_key,
            config.ollama_base_url,
        ]
    )

    runtime = Runtime(config)
    # Before startup — readiness is False (UNINITIALIZED phase).
    assert runtime.readiness() is False

    await runtime.startup()
    # After startup — provider_router auto-registered → readiness True.
    assert runtime.readiness() is True
    await runtime.shutdown()


# =====================================================================
# 4. birth() returns a snapshot dict with the required keys
# =====================================================================


REQUIRED_SNAPSHOT_KEYS = {
    "identity",
    "seed_version",
    "runtime_ready",
    "mock_available",
    "agent_loop_ok",
    "memory_ok",
    "audit_ok",
}


async def test_runtime_birth_returns_snapshot() -> None:
    """birth() returns a dict with all required birth-state fields."""
    runtime = Runtime(SeedConfig())
    snapshot = await runtime.birth()

    assert isinstance(snapshot, dict)
    # Every required key is present.
    assert REQUIRED_SNAPSHOT_KEYS.issubset(snapshot.keys())
    # Identity is an AgentIdentity carrying a seed_version.
    assert isinstance(snapshot["identity"], AgentIdentity)
    assert snapshot["seed_version"] == snapshot["identity"].seed_version
    assert snapshot["seed_version"]
    # All four birth booleans are True in CORE_ONLY mode.
    assert snapshot["runtime_ready"] is True
    assert snapshot["mock_available"] is True
    assert snapshot["agent_loop_ok"] is True
    assert snapshot["memory_ok"] is True
    assert snapshot["audit_ok"] is True
    # No external providers in CORE_ONLY mode.
    assert snapshot["external_providers"] == []
    # Bootstrap source recorded.
    assert snapshot["bootstrap_source"] == "auto"
    # born_at is an ISO timestamp string.
    assert isinstance(snapshot["born_at"], str)
    assert snapshot["born_at"]
    await runtime.shutdown()


# =====================================================================
# 5. After birth, an Agent can run a task end-to-end
# =====================================================================


async def test_runtime_birth_agent_can_run_task() -> None:
    """After birth(), Agent.run('hello') succeeds using the Mock provider.

    This proves the birth contract: the universal agent loop can execute
    a no-op task using ONLY the auto-registered router + Mock. No
    external provider was registered.
    """
    runtime = Runtime(SeedConfig())
    snapshot = await runtime.birth()
    assert snapshot["agent_loop_ok"] is True

    # Construct an Agent against the born runtime and run a task.
    identity = AgentIdentity(name="birth-runner")
    agent = Agent(identity, runtime)
    response = await agent.run("hello")

    # Mock provider echoes "[mock] hello".
    assert response.content == "[mock] hello"
    assert response.execution_id
    assert response.correlation_id
    # The auto-registered router has the Mock provider in its registry.
    router = runtime.get_component(COMPONENT_PROVIDER_ROUTER)
    assert router is not None
    assert router.registry.get("mock") is not None
    await runtime.shutdown()


# =====================================================================
# 6. birth() reports external_providers == [] in CORE_ONLY mode
# =====================================================================


async def test_runtime_birth_no_external_providers() -> None:
    """Birth snapshot reports an empty external_providers list when no
    external provider credentials are configured."""
    runtime = Runtime(SeedConfig())  # CORE_ONLY by default
    snapshot = await runtime.birth()

    assert snapshot["external_providers"] == []
    # And the config itself reports CORE_ONLY.
    assert runtime.config.is_core_only() is True
    await runtime.shutdown()


# =====================================================================
# 7. startup() logs the bootstrap event
# =====================================================================


async def test_runtime_birth_logs_bootstrap_event() -> None:
    """startup() emits a `runtime.bootstrap.provider_router` log event
    when it auto-registers the router. Caller-registered routers do
    NOT emit this event (no bootstrap happened)."""
    # Case A: auto-bootstrap path emits the event.
    runtime_a = Runtime(SeedConfig())
    with structlog.testing.capture_logs() as cap_a:
        await runtime_a.startup()
    bootstrap_events_a = [
        e for e in cap_a if e.get("event") == "runtime.bootstrap.provider_router"
    ]
    assert len(bootstrap_events_a) == 1
    assert bootstrap_events_a[0]["source"] == "auto"
    assert bootstrap_events_a[0]["mock"] is True
    await runtime_a.shutdown()

    # Case B: caller-provided router does NOT emit the bootstrap event.
    runtime_b = Runtime(SeedConfig())
    runtime_b.register(COMPONENT_PROVIDER_ROUTER, ProviderRouter())
    with structlog.testing.capture_logs() as cap_b:
        await runtime_b.startup()
    bootstrap_events_b = [
        e for e in cap_b if e.get("event") == "runtime.bootstrap.provider_router"
    ]
    assert len(bootstrap_events_b) == 0
    await runtime_b.shutdown()


# =====================================================================
# 8. birth() is idempotent: calling it twice yields consistent state
# =====================================================================


async def test_runtime_birth_idempotent() -> None:
    """birth() can be called multiple times; the second call reuses the
    already-started runtime and re-verifies the contract."""
    runtime = Runtime(SeedConfig())
    snap1 = await runtime.birth()
    snap2 = await runtime.birth()

    assert snap1["runtime_ready"] is True
    assert snap2["runtime_ready"] is True
    assert snap1["bootstrap_source"] == snap2["bootstrap_source"] == "auto"
    # Both calls report a born seed.
    assert snap1["agent_loop_ok"] is True
    assert snap2["agent_loop_ok"] is True
    await runtime.shutdown()
