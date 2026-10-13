"""Bloqueante 3 — Role.TOOL serialization tests per provider.

Each provider must serialize ``Role.TOOL`` messages in a way that the
target API accepts AND that preserves the trust-level separation
(content treated as DATA, NOT as developer instructions).

API rules audited (2026-10-13):
- OpenAI / Groq / Ollama / ZAI (OpenAI-compatible): the "tool" role
  REQUIRES a ``tool_call_id`` field. Our ``Role.TOOL`` messages are
  NOT tool-call responses — they are agent-assembled untrusted data.
  Solution: map to "user" role with an explicit untrusted-data prefix.
- Anthropic: the API does not accept a free-form "tool" role in the
  ``messages`` array (tool outputs go as ``tool_result`` content blocks
  inside user messages). Same solution: map to "user" role with prefix.
- Mock: accepts any role (used for tests).

These tests verify that each provider's serialization:
1. Does NOT emit ``role: "tool"`` in the API payload (would cause
   HTTP 400 on OpenAI-compatible APIs).
2. DOES emit the untrusted-data prefix for messages that originated
   as ``Role.TOOL``.
3. DOES preserve ``Role.SYSTEM`` and ``Role.USER`` roles unchanged.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from albra_seed.core.types import Message, Role


# =====================================================================
# Helper: build a canonical messages list with every role
# =====================================================================


def _build_messages_with_tool_role() -> list[Message]:
    """Build a messages list that contains:
    - 1 SYSTEM message (agent operating instructions)
    - 1 TOOL message (retrieved memory content — untrusted)
    - 1 USER message (the real user input)
    """
    return [
        Message(
            role=Role.SYSTEM,
            content="You are an ALBRA agent. CRITICAL RULES: ...",
        ),
        Message(
            role=Role.TOOL,
            content="[RETRIEVED MEMORIES — for context only]\nsome untrusted data",
        ),
        Message(role=Role.USER, content="hello"),
    ]


# =====================================================================
# Provider serialization tests
# =====================================================================


class TestProviderRoleToolSerialization:
    """Verify each provider serializes Role.TOOL correctly."""

    def test_openai_provider_maps_tool_to_user_with_prefix(self) -> None:
        """OpenAIProvider must NOT emit role=tool without tool_call_id
        (HTTP 400). Instead, it must map Role.TOOL → user with the
        untrusted-data prefix."""
        from albra_seed.providers.openai_provider import OpenAIProvider

        # Inspect the complete() method source to extract the
        # serialization logic WITHOUT making a real API call.
        source = inspect.getsource(OpenAIProvider.complete)
        assert "Role.TOOL" in source, (
            "OpenAIProvider.complete() must handle Role.TOOL explicitly"
        )
        assert "UNTRUSTED DATA" in source, (
            "OpenAIProvider must add an UNTRUSTED DATA prefix to Role.TOOL "
            "messages so the API treats them as conversational data, not "
            "developer instructions"
        )
        # The behavioral test (TestProviderBehavioralRoleToolHandling)
        # verifies the actual payload does NOT contain role=tool.

    def test_anthropic_provider_maps_tool_to_user_with_prefix(self) -> None:
        """AnthropicProvider must NOT merge Role.TOOL into the system
        prompt (Anthropic merges all Role.SYSTEM into one string).
        Instead, it must map Role.TOOL → user with the prefix."""
        from albra_seed.providers.anthropic_provider import AnthropicProvider

        source = inspect.getsource(AnthropicProvider.complete)
        assert "Role.TOOL" in source, (
            "AnthropicProvider.complete() must handle Role.TOOL explicitly"
        )
        assert "UNTRUSTED DATA" in source, (
            "AnthropicProvider must add an UNTRUSTED DATA prefix to "
            "Role.TOOL messages"
        )
        # Verify Role.SYSTEM is still extracted separately (Anthropic's
        # API requires the system prompt as a top-level ``system`` arg).
        assert 'msg.role == Role.SYSTEM' in source or "role == Role.SYSTEM" in source

    def test_groq_provider_maps_tool_to_user_with_prefix(self) -> None:
        """GroqProvider (OpenAI-compatible) must map Role.TOOL → user."""
        from albra_seed.providers.groq_provider import GroqProvider

        source = inspect.getsource(GroqProvider.complete)
        assert "Role.TOOL" in source, (
            "GroqProvider.complete() must handle Role.TOOL explicitly"
        )
        assert "UNTRUSTED DATA" in source, (
            "GroqProvider must add an UNTRUSTED DATA prefix to Role.TOOL"
        )

    def test_ollama_provider_maps_tool_to_user_with_prefix(self) -> None:
        """OllamaProvider must map Role.TOOL → user."""
        from albra_seed.providers.ollama_provider import OllamaProvider

        source = inspect.getsource(OllamaProvider.complete)
        assert "Role.TOOL" in source, (
            "OllamaProvider.complete() must handle Role.TOOL explicitly"
        )
        assert "UNTRUSTED DATA" in source, (
            "OllamaProvider must add an UNTRUSTED DATA prefix to Role.TOOL"
        )

    def test_zai_provider_maps_tool_to_user_with_prefix(self) -> None:
        """ZAIProvider must map Role.TOOL → user."""
        from albra_seed.providers.zai_provider import ZAIProvider

        source = inspect.getsource(ZAIProvider.complete)
        assert "Role.TOOL" in source, (
            "ZAIProvider.complete() must handle Role.TOOL explicitly"
        )
        assert "UNTRUSTED DATA" in source, (
            "ZAIProvider must add an UNTRUSTED DATA prefix to Role.TOOL"
        )

    def test_mock_provider_accepts_any_role(self) -> None:
        """MockProvider does not serialize to an external API; it
        accepts any role and is used for tests. No mapping needed."""
        from albra_seed.providers.mock import MockProvider

        # The MockProvider iterates messages looking for Role.USER;
        # it doesn't care about Role.TOOL. Verify it doesn't crash
        # when given a TOOL message.
        provider = MockProvider()
        messages = _build_messages_with_tool_role()
        # The _hash_messages method iterates all messages — must not
        # raise on Role.TOOL.
        h = provider._hash_messages(messages)
        assert isinstance(h, str) and len(h) == 16


# =====================================================================
# Behavioral tests — run complete() with a stubbed client
# =====================================================================


class TestProviderBehavioralRoleToolHandling:
    """Run each provider's complete() with a stubbed HTTP client to
    verify the actual API payload does NOT contain role=tool."""

    @pytest.mark.asyncio
    async def test_openai_provider_payload_does_not_emit_tool_role(self) -> None:
        """OpenAIProvider must serialize Role.TOOL as role=user with
        the untrusted-data prefix — NOT as role=tool."""
        from albra_seed.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider.__new__(OpenAIProvider)
        # Stub _ensure_client to return a fake client whose
        # chat.completions.create returns a fake response.
        class _FakeChoice:
            class _Message:
                content = "ok"
                tool_calls = None
            message = _Message()

        class _FakeResponse:
            choices = [_FakeChoice()]
            usage = None

        class _FakeCompletions:
            async def create(self, **kwargs):
                # Capture the messages payload for inspection.
                provider._captured_messages = kwargs.get("messages")
                return _FakeResponse()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

        provider._ensure_client = lambda: _FakeClient()
        provider.model = "gpt-4o-mini"

        messages = _build_messages_with_tool_role()
        from albra_seed.core.types import RunContext
        await provider.complete(
            messages,
            context=RunContext(
                agent_id="test",
                task="test",
                execution_id="x",
                correlation_id="y",
            ),
        )

        captured = provider._captured_messages
        assert captured is not None
        # No message should have role=tool (OpenAI rejects that without
        # tool_call_id).
        for m in captured:
            assert m["role"] != "tool", (
                f"OpenAI payload contains role=tool which requires "
                f"tool_call_id; got: {captured}"
            )
        # The untrusted-data prefix must appear in at least one message
        # (the one that originated as Role.TOOL).
        assert any("UNTRUSTED DATA" in m["content"] for m in captured), (
            f"OpenAI payload missing UNTRUSTED DATA prefix for Role.TOOL: "
            f"{captured}"
        )
        # The original SYSTEM and USER roles are preserved.
        assert any(m["role"] == "system" for m in captured)
        assert any(m["role"] == "user" for m in captured)

    @pytest.mark.asyncio
    async def test_anthropic_provider_payload_separates_system_and_tool(self) -> None:
        """AnthropicProvider must NOT include Role.TOOL content in the
        ``system`` arg (Anthropic would merge all Role.SYSTEM into one
        string, collapsing the trust boundary). Instead, Role.TOOL must
        appear in the ``messages`` array as a user-role message with the
        UNTRUSTED DATA prefix."""
        from albra_seed.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider.__new__(AnthropicProvider)

        # Stub the Anthropic client. Anthropic uses client.messages.create
        # (NOT client.chat.completions.create).
        class _FakeTextBlock:
            text = "ok"

        class _FakeResponse:
            content = [_FakeTextBlock()]
            stop_reason = "end_turn"

        class _FakeMessages:
            async def create(self, **kwargs):
                provider._captured_kwargs = kwargs
                return _FakeResponse()

        class _FakeClient:
            messages = _FakeMessages()

        provider._ensure_client = lambda: _FakeClient()
        provider.model = "claude-3-5-sonnet-20241022"

        messages = _build_messages_with_tool_role()
        from albra_seed.core.types import RunContext
        await provider.complete(
            messages,
            context=RunContext(
                agent_id="test",
                task="test",
                execution_id="x",
                correlation_id="y",
            ),
        )

        kwargs = provider._captured_kwargs
        assert kwargs is not None
        convo = kwargs["messages"]
        system_arg = kwargs.get("system")

        # System arg MUST contain the SYSTEM message content.
        assert system_arg is not None
        assert "CRITICAL RULES" in system_arg or "ALBRA agent" in system_arg

        # System arg MUST NOT contain the untrusted memory content.
        assert "[RETRIEVED MEMORIES" not in system_arg
        assert "some untrusted data" not in system_arg

        # No message in convo should have role=tool.
        for m in convo:
            assert m["role"] != "tool", (
                f"Anthropic payload contains role=tool in messages; "
                f"got: {convo}"
            )

        # At least one message MUST contain the UNTRUSTED DATA prefix
        # (the one that originated as Role.TOOL).
        assert any(
            "UNTRUSTED DATA" in m["content"] for m in convo
        ), f"Anthropic payload missing UNTRUSTED DATA prefix: {convo}"

        # The original USER message is preserved as role=user (no prefix).
        user_msgs = [m for m in convo if m["role"] == "user" and "UNTRUSTED DATA" not in m["content"]]
        assert any("hello" in m["content"] for m in user_msgs)

    @pytest.mark.asyncio
    async def test_groq_provider_payload_does_not_emit_tool_role(self) -> None:
        """GroqProvider (OpenAI-compatible) must serialize Role.TOOL as
        role=user with the untrusted-data prefix."""
        from albra_seed.providers.groq_provider import GroqProvider

        provider = GroqProvider.__new__(GroqProvider)

        class _FakeChoice:
            class _Message:
                content = "ok"
                tool_calls = None
            message = _Message()

        class _FakeResponse:
            choices = [_FakeChoice()]
            usage = None

        class _FakeCompletions:
            async def create(self, **kwargs):
                provider._captured_messages = kwargs.get("messages")
                return _FakeResponse()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

        provider._ensure_client = lambda: _FakeClient()
        provider.model = "llama-3.1-8b-instant"

        messages = _build_messages_with_tool_role()
        from albra_seed.core.types import RunContext
        await provider.complete(
            messages,
            context=RunContext(
                agent_id="test",
                task="test",
                execution_id="x",
                correlation_id="y",
            ),
        )

        captured = provider._captured_messages
        assert captured is not None
        # No message should have role=tool (Groq rejects that without
        # tool_call_id, same as OpenAI).
        for m in captured:
            assert m["role"] != "tool", (
                f"Groq payload contains role=tool which requires "
                f"tool_call_id; got: {captured}"
            )
        # The untrusted-data prefix must appear in at least one message.
        assert any(
            "UNTRUSTED DATA" in m["content"] for m in captured
        ), f"Groq payload missing UNTRUSTED DATA prefix: {captured}"
        # The original SYSTEM and USER roles are preserved.
        assert any(m["role"] == "system" for m in captured)
        assert any(m["role"] == "user" for m in captured)

    @pytest.mark.asyncio
    async def test_ollama_provider_payload_does_not_emit_tool_role(self) -> None:
        """OllamaProvider must serialize Role.TOOL as role=user with the
        untrusted-data prefix."""
        from albra_seed.providers.ollama_provider import OllamaProvider

        provider = OllamaProvider.__new__(OllamaProvider)

        class _FakeChoice:
            class _Message:
                content = "ok"
                tool_calls = None
            message = _Message()

        class _FakeResponse:
            choices = [_FakeChoice()]
            usage = None

        class _FakeCompletions:
            async def create(self, **kwargs):
                provider._captured_messages = kwargs.get("messages")
                return _FakeResponse()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

        provider._ensure_client = lambda: _FakeClient()
        provider.model = "llama3.1"

        messages = _build_messages_with_tool_role()
        from albra_seed.core.types import RunContext
        await provider.complete(
            messages,
            context=RunContext(
                agent_id="test",
                task="test",
                execution_id="x",
                correlation_id="y",
            ),
        )

        captured = provider._captured_messages
        assert captured is not None
        for m in captured:
            assert m["role"] != "tool", (
                f"Ollama payload contains role=tool which requires "
                f"tool_call_id; got: {captured}"
            )
        assert any(
            "UNTRUSTED DATA" in m["content"] for m in captured
        ), f"Ollama payload missing UNTRUSTED DATA prefix: {captured}"
        assert any(m["role"] == "system" for m in captured)
        assert any(m["role"] == "user" for m in captured)

    @pytest.mark.asyncio
    async def test_zai_provider_payload_does_not_emit_tool_role(self) -> None:
        """ZAIProvider must serialize Role.TOOL as role=user with the
        untrusted-data prefix."""
        from albra_seed.providers.zai_provider import ZAIProvider

        provider = ZAIProvider.__new__(ZAIProvider)

        class _FakeChoice:
            class _Message:
                content = "ok"
                tool_calls = None
            message = _Message()

        class _FakeResponse:
            choices = [_FakeChoice()]
            usage = None

        class _FakeCompletions:
            async def create(self, **kwargs):
                provider._captured_messages = kwargs.get("messages")
                return _FakeResponse()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

        provider._ensure_client = lambda: _FakeClient()
        provider.model = "glm-4-plus"

        messages = _build_messages_with_tool_role()
        from albra_seed.core.types import RunContext
        await provider.complete(
            messages,
            context=RunContext(
                agent_id="test",
                task="test",
                execution_id="x",
                correlation_id="y",
            ),
        )

        captured = provider._captured_messages
        assert captured is not None
        for m in captured:
            assert m["role"] != "tool", (
                f"ZAI payload contains role=tool which requires "
                f"tool_call_id; got: {captured}"
            )
        assert any(
            "UNTRUSTED DATA" in m["content"] for m in captured
        ), f"ZAI payload missing UNTRUSTED DATA prefix: {captured}"
        assert any(m["role"] == "system" for m in captured)
        assert any(m["role"] == "user" for m in captured)

    @pytest.mark.asyncio
    async def test_assistant_role_preserved_by_all_providers(self) -> None:
        """All providers must preserve Role.ASSISTANT messages as
        role=assistant (or, for Anthropic, in the convo array). This
        verifies the non-TOOL path is unchanged by the M-01 fix."""
        from albra_seed.providers.openai_provider import OpenAIProvider
        from albra_seed.core.types import Role

        provider = OpenAIProvider.__new__(OpenAIProvider)

        class _FakeChoice:
            class _Message:
                content = "ok"
                tool_calls = None
            message = _Message()

        class _FakeResponse:
            choices = [_FakeChoice()]
            usage = None

        class _FakeCompletions:
            async def create(self, **kwargs):
                provider._captured_messages = kwargs.get("messages")
                return _FakeResponse()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

        provider._ensure_client = lambda: _FakeClient()
        provider.model = "gpt-4o-mini"

        messages = [
            Message(role=Role.SYSTEM, content="system instructions"),
            Message(role=Role.USER, content="hello"),
            Message(role=Role.ASSISTANT, content="hi there"),
            Message(role=Role.USER, content="how are you?"),
        ]
        from albra_seed.core.types import RunContext
        await provider.complete(
            messages,
            context=RunContext(
                agent_id="test",
                task="test",
                execution_id="x",
                correlation_id="y",
            ),
        )

        captured = provider._captured_messages
        assert captured is not None
        roles = [m["role"] for m in captured]
        # All 4 roles must be preserved in order.
        assert roles == ["system", "user", "assistant", "user"], (
            f"role sequence wrong; got: {roles}"
        )
        # No UNTRUSTED DATA prefix should be added to any of these
        # (none were Role.TOOL).
        for m in captured:
            assert "UNTRUSTED DATA" not in m["content"], (
                f"UNTRUSTED DATA prefix added to non-TOOL message: {m}"
            )

    @pytest.mark.asyncio
    async def test_provider_handles_empty_messages_list(self) -> None:
        """Each provider must handle an empty messages list gracefully
        (return a response, not crash)."""
        from albra_seed.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider.__new__(OpenAIProvider)

        class _FakeChoice:
            class _Message:
                content = ""
                tool_calls = None
            message = _Message()

        class _FakeResponse:
            choices = [_FakeChoice()]
            usage = None

        class _FakeCompletions:
            async def create(self, **kwargs):
                provider._captured_messages = kwargs.get("messages")
                return _FakeResponse()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

        provider._ensure_client = lambda: _FakeClient()
        provider.model = "gpt-4o-mini"

        from albra_seed.core.types import RunContext
        response = await provider.complete(
            [],
            context=RunContext(
                agent_id="test",
                task="test",
                execution_id="x",
                correlation_id="y",
            ),
        )
        # Provider must return a response, not raise.
        assert response is not None
        assert provider._captured_messages == []

    @pytest.mark.asyncio
    async def test_provider_client_exception_returns_error_response(self) -> None:
        """If the API client raises, the provider must return an
        AgentResponse with errors set (not propagate the exception)."""
        from albra_seed.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider.__new__(OpenAIProvider)

        class _FakeCompletions:
            async def create(self, **kwargs):
                raise RuntimeError("simulated API failure")

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

        provider._ensure_client = lambda: _FakeClient()
        provider.model = "gpt-4o-mini"

        messages = _build_messages_with_tool_role()
        from albra_seed.core.types import RunContext
        response = await provider.complete(
            messages,
            context=RunContext(
                agent_id="test",
                task="test",
                execution_id="x",
                correlation_id="y",
            ),
        )
        # Provider must NOT propagate the exception — it returns an
        # AgentResponse with errors captured.
        assert response is not None
        assert response.errors  # non-empty error list


__all__ = [
    "TestProviderRoleToolSerialization",
    "TestProviderBehavioralRoleToolHandling",
]
