"""Tests for the NVIDIA NIM provider adapter.

These tests verify:
- Client construction with the expected configuration.
- Chat completions request format (OpenAI-compatible).
- Response conversion to AgentResponse.
- API key missing → NotImplementedError.
- API key invalid (401) → error response, no retry.
- Timeout → error response, retry attempted.
- Rate limit (429) → error response, retry attempted.
- Provider error (500) → error response, retry attempted.
- Empty/invalid response → error response.
- Secrets do NOT appear in error messages.

All tests use mocks — NO real NVIDIA API calls are made.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from albra_seed.core.types import AgentResponse, Message, Role, RunContext


# =====================================================================
# Helpers
# =====================================================================


def _make_messages() -> list[Message]:
    return [
        Message(role=Role.SYSTEM, content="You are a helpful assistant."),
        Message(role=Role.USER, content="Which number is larger, 9.11 or 9.8?"),
    ]


def _make_fake_choice(content: str = "9.8 is larger.", tool_calls=None):
    """Build a fake OpenAI choice object."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    return choice


def _make_fake_response(content: str = "9.8 is larger."):
    """Build a fake OpenAI chat completion response."""
    response = MagicMock()
    response.choices = [_make_fake_choice(content)]
    response.usage = None
    return response


class _FakeAsyncOpenAI:
    """A fake AsyncOpenAI client that returns canned responses."""

    def __init__(self, *, response=None, exc=None, stream_chunks=None):
        self._response = response
        self._exc = exc
        self._stream_chunks = stream_chunks
        self.call_count = 0
        self.chat = MagicMock()
        self.chat.completions = MagicMock()
        self.chat.completions.create = self._create

    async def _create(self, **kwargs):
        self.call_count += 1
        if self._exc:
            raise self._exc
        if self._stream_chunks is not None:
            # Return an async iterator for streaming
            async def _stream():
                for chunk in self._stream_chunks:
                    yield chunk
            return _stream()
        return self._response


# =====================================================================
# Tests
# =====================================================================


class TestNVIDIAProviderConstruction:
    """Verify the provider constructs correctly with the expected config."""

    def test_construction_with_explicit_key(self, monkeypatch):
        """Provider constructs with api_key, model, base_url, and params."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        p = NVIDIAProvider(
            model="z-ai/glm-5.3",
            base_url="https://integrate.api.nvidia.com/v1",
            timeout=30.0,
            temperature=0.7,
            top_p=0.9,
            max_tokens=512,
            stream=True,
        )
        assert p.name == "nvidia"
        assert p.is_external is True
        assert p.is_mock is False
        assert p.model == "z-ai/glm-5.3"
        assert p.base_url == "https://integrate.api.nvidia.com/v1"
        assert p.timeout == 30.0
        assert p.temperature == 0.7
        assert p.top_p == 0.9
        assert p.max_tokens == 512
        assert p.stream is True
        assert p.max_retries == 2

    def test_construction_defaults(self, monkeypatch):
        """Defaults match the NVIDIA NIM reference config."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider, DEFAULT_NVIDIA_BASE_URL

        p = NVIDIAProvider()
        assert p.model == "z-ai/glm-5.3"
        assert p.base_url == DEFAULT_NVIDIA_BASE_URL
        assert p.temperature == 0.5
        assert p.top_p == 1.0
        assert p.max_tokens == 1024
        assert p.stream is False
        assert p.timeout == 60.0

    def test_construction_without_key_raises(self, monkeypatch):
        """Missing API key raises NotImplementedError (fail-closed)."""
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        with pytest.raises(NotImplementedError, match="NVIDIA_API_KEY"):
            NVIDIAProvider()

    def test_construction_with_explicit_key_overrides_env(self, monkeypatch):
        """Explicit api_key takes precedence over env var."""
        monkeypatch.setenv("NVIDIA_API_KEY", "env-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        p = NVIDIAProvider(api_key="explicit-key")
        assert p._api_key == "explicit-key"


class TestNVIDIAProviderComplete:
    """Verify the complete() method builds the right request and parses
    the response correctly."""

    @pytest.mark.asyncio
    async def test_complete_success(self, monkeypatch):
        """A successful completion returns an AgentResponse with content."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        p = NVIDIAProvider()
        fake_client = _FakeAsyncOpenAI(response=_make_fake_response("9.8 is larger."))
        p._client = fake_client

        result = await p.complete(_make_messages())

        assert isinstance(result, AgentResponse)
        assert result.content == "9.8 is larger."
        assert result.errors == []
        assert result.metadata["provider"] == "nvidia"
        assert result.metadata["model"] == "z-ai/glm-5.3"
        assert "latency_ms" in result.metadata

    @pytest.mark.asyncio
    async def test_complete_passes_correct_kwargs(self, monkeypatch):
        """The provider passes model, messages, temperature, top_p, max_tokens."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        captured_kwargs = {}

        async def _capture_create(**kwargs):
            captured_kwargs.update(kwargs)
            return _make_fake_response("ok")

        p = NVIDIAProvider()
        p._client = MagicMock()
        p._client.chat.completions.create = _capture_create

        await p.complete(_make_messages())

        assert captured_kwargs["model"] == "z-ai/glm-5.3"
        assert captured_kwargs["temperature"] == 0.5
        assert captured_kwargs["top_p"] == 1.0
        assert captured_kwargs["max_tokens"] == 1024
        assert captured_kwargs["stream"] is False
        # Messages should be serialized (Role.SYSTEM → "system", etc.)
        assert captured_kwargs["messages"][0]["role"] == "system"
        assert captured_kwargs["messages"][1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_complete_empty_response(self, monkeypatch):
        """An empty response returns an AgentResponse with empty content."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        p = NVIDIAProvider()
        fake_response = MagicMock()
        fake_response.choices = [_make_fake_choice(content="")]
        fake_response.usage = None
        p._client = _FakeAsyncOpenAI(response=fake_response)

        result = await p.complete(_make_messages())

        assert result.content == ""
        assert result.errors == []


class TestNVIDIAProviderErrors:
    """Verify error handling — no retries for auth, retries for transient."""

    @pytest.mark.asyncio
    async def test_auth_error_no_retry(self, monkeypatch):
        """401/403 errors are NOT retried — fail immediately."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-invalid-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        # Build a fake 401 error
        try:
            import openai
            exc = openai.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(status_code=401),
                body=None,
            )
        except Exception:
            pytest.skip("openai SDK not available for this test")

        p = NVIDIAProvider(max_retries=3)
        p._client = _FakeAsyncOpenAI(exc=exc)

        result = await p.complete(_make_messages())

        assert result.errors
        # Should NOT have retried (call_count == 1)
        assert p._client.call_count == 1
        # The error message must NOT contain the API key
        for err in result.errors:
            assert "nvapi-invalid-key" not in err
            assert "nvapi-" not in err

    @pytest.mark.asyncio
    async def test_timeout_retried(self, monkeypatch):
        """Timeout errors are retried up to max_retries."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        try:
            import openai
            exc = openai.APITimeoutError(request=MagicMock())
        except Exception:
            pytest.skip("openai SDK not available")

        p = NVIDIAProvider(max_retries=2)
        p._client = _FakeAsyncOpenAI(exc=exc)

        # Patch sleep to avoid real waiting
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await p.complete(_make_messages())

        assert result.errors
        # Should have retried 3 times (1 initial + 2 retries)
        assert p._client.call_count == 3

    @pytest.mark.asyncio
    async def test_rate_limit_retried(self, monkeypatch):
        """429 rate limit errors are retried."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        try:
            import openai
            exc = openai.RateLimitError(
                message="Rate limit exceeded",
                response=MagicMock(status_code=429),
                body=None,
            )
        except Exception:
            pytest.skip("openai SDK not available")

        p = NVIDIAProvider(max_retries=1)
        p._client = _FakeAsyncOpenAI(exc=exc)

        with patch("asyncio.sleep", new=AsyncMock()):
            result = await p.complete(_make_messages())

        assert result.errors
        # 1 initial + 1 retry = 2 calls
        assert p._client.call_count == 2

    @pytest.mark.asyncio
    async def test_server_error_retried(self, monkeypatch):
        """5xx server errors are retried."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        try:
            import openai
            exc = openai.InternalServerError(
                message="Internal server error",
                response=MagicMock(status_code=503),
                body=None,
            )
        except Exception:
            pytest.skip("openai SDK not available")

        p = NVIDIAProvider(max_retries=1)
        p._client = _FakeAsyncOpenAI(exc=exc)

        with patch("asyncio.sleep", new=AsyncMock()):
            result = await p.complete(_make_messages())

        assert result.errors
        assert p._client.call_count == 2

    @pytest.mark.asyncio
    async def test_secrets_not_in_error_messages(self, monkeypatch):
        """Error messages must NOT contain the API key."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-SUPERSECRETKEY1234567890")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        p = NVIDIAProvider()
        # Simulate a generic error that might include the key in its message
        p._client = _FakeAsyncOpenAI(exc=RuntimeError("Request to nvapi-SUPERSECRETKEY1234567890 failed"))

        result = await p.complete(_make_messages())

        assert result.errors
        for err in result.errors:
            assert "SUPERSECRETKEY" not in err
            assert "nvapi-SUPER" not in err
            # The redactor should have replaced the key
            assert "nvapi-[REDACTED]" in err or "nvapi-" not in err


class TestNVIDIAProviderHealth:
    """Verify the health() method."""

    @pytest.mark.asyncio
    async def test_health_success(self, monkeypatch):
        """health() returns True when the provider responds."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        p = NVIDIAProvider()
        fake_response = MagicMock()
        fake_response.choices = [_make_fake_choice("ok")]
        p._client = _FakeAsyncOpenAI(response=fake_response)

        result = await p.health()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_failure(self, monkeypatch):
        """health() returns False on error."""
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
        from albra_seed.providers.nvidia_provider import NVIDIAProvider

        p = NVIDIAProvider()
        p._client = _FakeAsyncOpenAI(exc=RuntimeError("connection failed"))

        result = await p.health()
        assert result is False


__all__ = [
    "TestNVIDIAProviderConstruction",
    "TestNVIDIAProviderComplete",
    "TestNVIDIAProviderErrors",
    "TestNVIDIAProviderHealth",
]
