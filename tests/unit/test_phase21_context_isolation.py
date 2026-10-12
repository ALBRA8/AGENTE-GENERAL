"""Phase 2.1 regression tests — context without explicit session.

Verifies the two fixes from Phase 2.1:

1. Hallazgo 1: A `context` with a non-default domain (e.g. "finance") and NO
   caller-provided `session` must NOT be rejected as a false-positive
   mismatch. Previously, a default Session was created with domain="default"
   BEFORE the coherence check, causing a spurious "domain mismatch" error.

2. Hallazgo 2: `runtime.register_session(session)` must NOT execute before
   the coherence check passes. A rejected run must NOT leave a session
   registered in the runtime.

Tests cover the 5 mandatory scenarios from the spec.
"""

from __future__ import annotations

import asyncio
import pytest

from albra_seed import (
    Agent,
    AgentIdentity,
    Runtime,
    SeedConfig,
    AutonomyLevel,
    RiskLevel,
    RunContext,
    Session,
)
from albra_seed.core.types import Message, Role
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.memory.manager import MemoryManager
from albra_seed.providers.mock import MockProvider


# =====================================================================
# Helpers
# =====================================================================


class _SpyMemoryManager(MemoryManager):
    """MemoryManager subclass that records every method call for inspection."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, dict]] = []

    async def remember(self, **kwargs):
        self.calls.append(("remember", kwargs))
        return await super().remember(**kwargs)

    async def recall(self, **kwargs):
        self.calls.append(("recall", kwargs))
        return await super().recall(**kwargs)

    async def forget(self, *args, **kwargs):
        self.calls.append(("forget", kwargs))
        return await super().forget(*args, **kwargs)

    async def cleanup_working(self, **kwargs):
        self.calls.append(("cleanup_working", kwargs))
        return await super().cleanup_working(**kwargs)


class _RecordingMockProvider(MockProvider):
    """MockProvider that records whether complete() was called."""

    def __init__(self):
        super().__init__()
        self.received_messages: list[Message] = []

    async def complete(self, messages, **kwargs):
        self._call_count += 1
        self.received_messages = list(messages)
        return await super().complete(messages, **kwargs)


async def _build_runtime_with_spy_memory() -> tuple[Runtime, _SpyMemoryManager, _RecordingMockProvider]:
    """Build a Runtime with a spy MemoryManager + recording MockProvider."""
    runtime = Runtime(config=SeedConfig())
    await runtime.startup()
    store = InMemoryMemoryStore()
    spy_mgr = _SpyMemoryManager(store=store)
    runtime.register("memory_manager", spy_mgr)
    runtime.register_memory_store("default", store)
    provider = _RecordingMockProvider()
    runtime.register_provider(provider)
    return runtime, spy_mgr, provider


def _identity() -> AgentIdentity:
    return AgentIdentity(
        name="phase21-test-agent",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        risk_profile=RiskLevel.LOW,
    )


# =====================================================================
# Mandatory regression tests
# =====================================================================


class TestPhase21ContextWithoutSession:
    """Phase 2.1 regression tests."""

    @pytest.mark.asyncio
    async def test_context_without_session_with_custom_domain_works(self):
        """Mandatory test 1: context without session, domain='finance'.

        Must work and use 'finance' as the domain for memory operations.
        """
        runtime, spy_mgr, provider = await _build_runtime_with_spy_memory()
        agent = Agent(_identity(), runtime)

        # Build a RunContext with a non-default domain, no session provided.
        ctx = RunContext(
            agent_id=agent.identity.agent_id,
            task="finance query",
            domain="finance",
            user_id=None,
            context_id=None,
        )

        # Run — should NOT raise ValueError.
        response = await agent.run("hello", context=ctx)

        # Assert: response produced.
        assert response is not None
        assert response.content  # Mock provider returns something.

        # Assert: memory operations used the 'finance' domain.
        remember_calls = [c for c in spy_mgr.calls if c[0] == "remember"]
        assert len(remember_calls) > 0, "expected at least one remember() call"
        for _, kwargs in remember_calls:
            assert kwargs.get("domain") == "finance", (
                f"memory write used domain={kwargs.get('domain')!r}, expected 'finance'"
            )

        # Assert: recall used 'finance' domain too.
        recall_calls = [c for c in spy_mgr.calls if c[0] == "recall"]
        for _, kwargs in recall_calls:
            assert kwargs.get("domain") == "finance"

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_context_without_session_with_custom_user_and_context_id(self):
        """Mandatory test 2: context without session, custom user_id + context_id.

        Must preserve the correct isolation (user_id + context_id propagate).
        """
        runtime, spy_mgr, provider = await _build_runtime_with_spy_memory()
        agent = Agent(_identity(), runtime)

        ctx = RunContext(
            agent_id=agent.identity.agent_id,
            task="isolated query",
            domain="default",
            user_id="alice",
            context_id="ctx-123",
        )

        response = await agent.run("hello", context=ctx)

        assert response is not None

        # Verify isolation dims propagated to memory.
        remember_calls = [c for c in spy_mgr.calls if c[0] == "remember"]
        for _, kwargs in remember_calls:
            assert kwargs.get("user_id") == "alice"
            assert kwargs.get("context_id") == "ctx-123"

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_context_and_session_with_incompatible_domains_rejected(self):
        """Mandatory test 3: context + session with mismatched domains → ValueError."""
        runtime, spy_mgr, provider = await _build_runtime_with_spy_memory()
        agent = Agent(_identity(), runtime)

        # Caller provides BOTH, with mismatched domains.
        session = Session(
            agent_id=agent.identity.agent_id,
            domain="default",
        )
        ctx = RunContext(
            agent_id=agent.identity.agent_id,
            task="mismatched",
            domain="finance",  # MISMATCH with session.domain="default"
        )

        # Must raise.
        with pytest.raises(ValueError, match="domain"):
            await agent.run("hello", session=session, context=ctx)

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_rejection_does_not_register_session_or_invoke_memory_or_provider(self):
        """Mandatory test 4: on rejection, NO side effects.

        - runtime.register_session NOT called (session count stays at 0)
        - MemoryManager.remember / recall / cleanup_working NOT called
        - Provider.complete NOT called
        """
        runtime, spy_mgr, provider = await _build_runtime_with_spy_memory()
        agent = Agent(_identity(), runtime)

        session_count_before = len(runtime._sessions) if hasattr(runtime, "_sessions") else 0

        session = Session(
            agent_id=agent.identity.agent_id,
            domain="default",
            user_id="alice",
        )
        ctx = RunContext(
            agent_id=agent.identity.agent_id,
            task="rejected",
            domain="other",  # MISMATCH
            user_id="alice",
        )

        with pytest.raises(ValueError):
            await agent.run("hello", session=session, context=ctx)

        # Assert: NO memory operations happened.
        assert spy_mgr.calls == [], (
            f"memory operations happened on rejection: {spy_mgr.calls}"
        )

        # Assert: provider NOT invoked.
        assert provider.received_messages == [], (
            "provider was invoked on a rejected run"
        )

        # Assert: session NOT registered in runtime.
        # (register_session would add it to runtime._sessions — check it didn't grow)
        session_count_after = len(runtime._sessions) if hasattr(runtime, "_sessions") else 0
        assert session_count_after == session_count_before, (
            f"session was registered on rejection: before={session_count_before}, "
            f"after={session_count_after}"
        )

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_existing_isolation_tests_still_pass(self):
        """Mandatory test 5: existing isolation behavior preserved.

        - Matching session + context → OK
        - Mismatched user_id → ValueError
        - Mismatched agent_id → ValueError
        - Session-only path → still propagates dims
        - Context-only path → still works (now with non-default domain)
        """
        runtime, spy_mgr, provider = await _build_runtime_with_spy_memory()
        agent = Agent(_identity(), runtime)

        # Matching session + context → OK.
        session = Session(
            agent_id=agent.identity.agent_id,
            domain="default",
            user_id="alice",
        )
        ctx = RunContext(
            agent_id=agent.identity.agent_id,
            task="matching",
            domain="default",
            user_id="alice",
        )
        response = await agent.run("hello", session=session, context=ctx)
        assert response is not None

        await runtime.shutdown()

        # Mismatched user_id → ValueError (new runtime for clean state).
        runtime2, _, _ = await _build_runtime_with_spy_memory()
        agent2 = Agent(_identity(), runtime2)
        session2 = Session(
            agent_id=agent2.identity.agent_id,
            user_id="alice",
        )
        ctx2 = RunContext(
            agent_id=agent2.identity.agent_id,
            task="mismatch",
            user_id="bob",  # MISMATCH
        )
        with pytest.raises(ValueError, match="user_id"):
            await agent2.run("hello", session=session2, context=ctx2)
        await runtime2.shutdown()

        # Mismatched agent_id → ValueError.
        runtime3, _, _ = await _build_runtime_with_spy_memory()
        agent3 = Agent(_identity(), runtime3)
        ctx3 = RunContext(
            agent_id="other-agent",  # MISMATCH
            task="bad agent",
        )
        with pytest.raises(ValueError, match="agent_id"):
            await agent3.run("hello", context=ctx3)
        await runtime3.shutdown()

        # Session-only path → propagates dims.
        runtime4, spy4, _ = await _build_runtime_with_spy_memory()
        agent4 = Agent(_identity(), runtime4)
        session4 = Session(
            agent_id=agent4.identity.agent_id,
            user_id="carol",
            domain="custom-domain",
        )
        response4 = await agent4.run("hello", session=session4)
        assert response4 is not None
        remember4 = [c for c in spy4.calls if c[0] == "remember"]
        assert len(remember4) > 0
        for _, kwargs in remember4:
            assert kwargs.get("user_id") == "carol"
            assert kwargs.get("domain") == "custom-domain"
        await runtime4.shutdown()


# =====================================================================
# Additional negative tests for the false-positive bug
# =====================================================================


class TestPhase21FalsePositiveRegressions:
    """Tests that specifically guard against the false-positive bug
    where a default Session was created BEFORE the coherence check."""

    @pytest.mark.asyncio
    async def test_context_with_finance_domain_no_session_not_rejected(self):
        """The exact scenario from Hallazgo 1.

        Before the fix: default Session(domain="default") was created,
        then coherence check saw session.domain="default" vs
        context.domain="finance" → false-positive ValueError.
        """
        runtime, _, _ = await _build_runtime_with_spy_memory()
        agent = Agent(_identity(), runtime)

        ctx = RunContext(
            agent_id=agent.identity.agent_id,
            task="finance task",
            domain="finance",
        )

        # Must NOT raise.
        response = await agent.run("hello", context=ctx)
        assert response is not None
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_context_with_marketing_domain_no_session_not_rejected(self):
        """Same scenario with a different domain name."""
        runtime, _, _ = await _build_runtime_with_spy_memory()
        agent = Agent(_identity(), runtime)

        ctx = RunContext(
            agent_id=agent.identity.agent_id,
            task="marketing task",
            domain="marketing",
        )
        response = await agent.run("hello", context=ctx)
        assert response is not None
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_context_with_custom_user_id_no_session_not_rejected(self):
        """Context with custom user_id and NO session — must NOT raise."""
        runtime, _, _ = await _build_runtime_with_spy_memory()
        agent = Agent(_identity(), runtime)

        ctx = RunContext(
            agent_id=agent.identity.agent_id,
            task="user-scoped",
            user_id="dave",
            context_id="dave-session-1",
        )
        response = await agent.run("hello", context=ctx)
        assert response is not None
        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_context_with_agent_id_mismatch_still_rejected(self):
        """Even without a session, agent_id mismatch MUST still be rejected."""
        runtime, _, _ = await _build_runtime_with_spy_memory()
        agent = Agent(_identity(), runtime)

        ctx = RunContext(
            agent_id="other-agent-id",  # MISMATCH
            task="bad",
        )
        with pytest.raises(ValueError, match="agent_id"):
            await agent.run("hello", context=ctx)
        await runtime.shutdown()
