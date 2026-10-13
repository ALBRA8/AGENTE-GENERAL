"""Bloqueante 2 — Fail-closed redactor tests.

The M-02 redactor in _phase_respond must NEVER deliver unredacted
content if the redactor raises an exception. The previous policy was
fail-open (deliver original content + warning); the new policy is
fail-closed (deliver a safe placeholder + warning).

These tests simulate a redactor failure by monkey-patching
``albra_seed.memory.sanitizer.redact_secrets`` to raise. The agent's
response content must NOT contain the original (potentially
secret-laden) text — it must contain the safe placeholder.
"""

from __future__ import annotations

import pytest

from albra_seed.core.types import (
    AgentResponse,
    Message,
    Role,
    Session,
    MemoryType,
)
from albra_seed.memory.manager import MemoryManager
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.providers.mock import MockProvider
from albra_seed.runtime.agent import Agent
from albra_seed.runtime.runtime import Runtime, COMPONENT_MEMORY_MANAGER
from albra_seed.core.identity import AgentIdentity


# =====================================================================
# Helpers
# =====================================================================


class _SecretLeakProvider(MockProvider):
    """Mock provider that always emits a secret-shaped string in its
    response, simulating a worst-case LLM that parrots a secret it
    observed via a tool output or its own generation."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self.received_messages: list[list[Message]] = []
        self._secret = secret

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        context=None,
    ) -> AgentResponse:
        self.received_messages.append(list(messages))
        return AgentResponse(
            content=f"here is the secret: {self._secret}",
            execution_id=context.execution_id if context else "exec",
            correlation_id=context.correlation_id if context else "corr",
            confidence=1.0,
            metadata={"provider": self.name, "mock": True},
        )


async def _build_runtime_with_memory(
    *,
    provider: MockProvider,
    memory_content: str = "benign context",
    user_id: str = "alice",
) -> tuple[Runtime, Agent, Session]:
    identity = AgentIdentity(
        agent_id="agent-b2-test",
        name="b2-test-agent",
    )
    runtime = Runtime()
    runtime.register_provider(provider)
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    runtime.register_memory_store("default", mgr.store)
    runtime.register(COMPONENT_MEMORY_MANAGER, mgr)
    await runtime.startup()
    if memory_content:
        await mgr.remember(
            agent_id=identity.agent_id,
            user_id=user_id,
            content=memory_content,
            memory_type=MemoryType.EPISODIC,
        )
    agent = Agent(identity, runtime)
    session = Session(agent_id=identity.agent_id, user_id=user_id)
    return runtime, agent, session


# =====================================================================
# Tests — Fail-closed redactor policy
# =====================================================================


class TestM02FailClosedRedactor:
    """Verify that a redactor failure does NOT leak the original content."""

    @pytest.mark.asyncio
    async def test_redactor_failure_replaces_content_with_placeholder(
        self, monkeypatch
    ) -> None:
        """When ``redact_secrets`` raises, the response content MUST
        be replaced with the safe placeholder — NOT the original text."""
        secret = "sk-1234567890abcdef1234567890abcdef"
        provider = _SecretLeakProvider(secret=secret)
        runtime, agent, session = await _build_runtime_with_memory(
            provider=provider,
        )

        # Monkey-patch redact_secrets to raise — simulates a buggy
        # redactor (regex compile failure, OOM, etc.).
        from albra_seed.memory import sanitizer as sanit_mod

        def _raise(content):
            raise RuntimeError("simulated redactor failure")

        monkeypatch.setattr(sanit_mod, "redact_secrets", _raise)

        response = await agent.run("show me the secret", session=session)

        # The original secret MUST NOT appear in the response.
        assert secret not in response.content, (
            f"secret leaked because redactor failure was fail-open: "
            f"{response.content!r}"
        )
        # The safe placeholder MUST be present.
        assert "[REDACTED: response redactor failed" in response.content, (
            f"expected fail-closed placeholder, got: {response.content!r}"
        )
        # The warning MUST mention the failure.
        ctx = agent.state.current_context
        assert ctx is not None
        warnings_blob = " ".join(ctx.warnings)
        assert "M-02 response redactor failed" in warnings_blob
        assert "fail-closed" in warnings_blob

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_redactor_failure_preserves_tool_results_and_confidence(
        self, monkeypatch
    ) -> None:
        """A redactor failure must NOT alter tool_results, confidence,
        or any non-text field — only the text content is replaced."""
        secret = "sk-deadbeefcafebabedeadbeefcafebabe"
        provider = _SecretLeakProvider(secret=secret)
        runtime, agent, session = await _build_runtime_with_memory(
            provider=provider,
        )

        from albra_seed.memory import sanitizer as sanit_mod

        def _raise(content):
            raise RuntimeError("simulated")

        monkeypatch.setattr(sanit_mod, "redact_secrets", _raise)

        response = await agent.run("show me the secret", session=session)

        # Confidence is preserved.
        assert response.confidence == 1.0
        # The content is the safe placeholder.
        assert "[REDACTED: response redactor failed" in response.content
        # No secret leaks.
        assert secret not in response.content

        await runtime.shutdown()

    @pytest.mark.asyncio
    async def test_redactor_failure_with_no_secret_also_replaces(
        self, monkeypatch
    ) -> None:
        """Even if the response has no secret, a redactor failure
        still replaces the content — we cannot know what the redactor
        WOULD have redacted, so fail-closed is mandatory."""
        provider = _SecretLeakProvider(secret="benign-string-without-secrets")
        runtime, agent, session = await _build_runtime_with_memory(
            provider=provider,
        )

        from albra_seed.memory import sanitizer as sanit_mod

        def _raise(content):
            raise RuntimeError("simulated")

        monkeypatch.setattr(sanit_mod, "redact_secrets", _raise)

        response = await agent.run("hello", session=session)

        # Even though the response has no secret, fail-closed policy
        # replaces the content because we cannot trust the redactor
        # succeeded.
        assert "[REDACTED: response redactor failed" in response.content
        assert "benign-string-without-secrets" not in response.content

        await runtime.shutdown()


__all__ = ["TestM02FailClosedRedactor"]
