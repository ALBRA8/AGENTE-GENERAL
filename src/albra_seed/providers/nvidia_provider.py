"""NVIDIA NIM provider — optional, lazily imported.

NVIDIA NIM (NVIDIA Inference Microservices) exposes an OpenAI-compatible
chat completions endpoint at ``https://integrate.api.nvidia.com/v1``.
This adapter uses the ``openai`` Python SDK pointed at that URL, exactly
like the ZAI/Groq/Ollama providers.

Configuration:
    api_key  — NVIDIA API key (env: NVIDIA_API_KEY)
    model    — default "z-ai/glm-5.3"
    base_url — default "https://integrate.api.nvidia.com/v1"
    timeout  — default 60.0 seconds
    temperature, top_p, max_tokens — passed through to chat.completions.create

Security:
    - The API key is NEVER logged, NEVER included in error messages,
      and NEVER sent to any endpoint other than ``base_url``.
    - The provider fails closed on missing/invalid credentials, timeouts,
      rate limits, and provider errors. It does NOT fabricate success.
    - Transient errors (5xx, timeouts, rate limits) are retried with
      exponential backoff up to ``max_retries`` (default 2). Auth errors
      (401/403) and validation errors (400) are NOT retried.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from albra_seed.core.base import LLMProvider
from albra_seed.core.types import (
    AgentResponse,
    Message,
    Role,
    RunContext,
    new_id,
)

DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


# Exception types we treat as transient (retryable). We resolve them
# lazily so that the openai SDK is not required at import time.
def _is_transient_error(exc: Exception) -> bool:
    """Classify whether an exception is worth retrying.

    Transient: 5xx server errors, timeouts, rate limits (429), connection
    errors. Non-transient: 401/403 (auth), 400 (validation), 404 (model
    not found). We never retry non-transient errors.
    """
    # Try openai-specific exception types first.
    try:
        import openai
        if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
            return True
        if isinstance(exc, openai.RateLimitError):
            return True
        if isinstance(exc, openai.InternalServerError):
            return True
        # APIStatusError with 5xx status code
        if isinstance(exc, openai.APIStatusError):
            status = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None
            )
            if status is not None and 500 <= int(status) < 600:
                return True
            return False
    except Exception:  # noqa: BLE001 — openai not installed or unexpected
        pass

    # Generic fallbacks (when openai SDK is not installed but a
    # network-level error was raised by the test harness).
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__.lower()
    if "timeout" in name or "connection" in name:
        return True
    return False


def _is_auth_error(exc: Exception) -> bool:
    """Classify auth errors (401/403) — never retried."""
    try:
        import openai
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return True
        if isinstance(exc, openai.APIStatusError):
            status = getattr(exc, "status_code", None) or getattr(
                getattr(exc, "response", None), "status_code", None
            )
            if status in (401, 403):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _safe_error_message(exc: Exception) -> str:
    """Return a safe error message that does NOT leak the API key.

    The openai SDK sometimes includes the URL (which may contain a key
    in misconfigured deployments) or the full request headers in error
    messages. We strip anything that looks like a Bearer token or
    ``nvapi-...`` key.
    """
    msg = f"{type(exc).__name__}: {exc}"
    # Strip any accidental key leakage. Never log "Bearer ..." or
    # "nvapi-..." values.
    import re
    msg = re.sub(r"Bearer\s+[A-Za-z0-9_\-.]{8,}", "Bearer [REDACTED]", msg)
    msg = re.sub(r"nvapi-[A-Za-z0-9_\-.]{8,}", "nvapi-[REDACTED]", msg)
    return msg


class NVIDIAProvider(LLMProvider):
    """LLM provider backed by NVIDIA NIM (OpenAI-compatible endpoint).

    Uses the ``openai`` Python SDK pointed at NVIDIA's integrate API.
    The provider is lazy — the SDK is only imported when the provider is
    instantiated, and the HTTP client is only built on the first
    ``complete()`` call.
    """

    name: str = "nvidia"
    is_mock: bool = False
    is_external: bool = True

    DEFAULT_MODEL = "z-ai/glm-5.3"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
        temperature: float = 0.5,
        top_p: float = 1.0,
        max_tokens: int = 1024,
        stream: bool = False,
    ) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise NotImplementedError(
                "nvidia provider requires the 'openai' package; "
                "install with `pip install albra-seed[openai]` or "
                "`pip install openai`"
            ) from exc

        import os

        resolved_key = api_key or os.environ.get("NVIDIA_API_KEY")
        if not resolved_key:
            raise NotImplementedError(
                "nvidia provider requires NVIDIA_API_KEY "
                "(pass api_key= or set the environment variable)"
            )

        self._api_key: str = resolved_key
        self.model: str = model or self.DEFAULT_MODEL
        self.base_url: str = base_url or DEFAULT_NVIDIA_BASE_URL
        self.timeout: float = float(timeout)
        self.max_retries: int = int(max_retries)
        self.temperature: float = float(temperature)
        self.top_p: float = float(top_p)
        self.max_tokens: int = int(max_tokens)
        self.stream: bool = bool(stream)
        self._client = None  # built lazily on first call

    # ------------------------------------------------------------------ #
    # Client lifecycle                                                   #
    # ------------------------------------------------------------------ #

    def _ensure_client(self) -> Any:
        """Build the AsyncOpenAI client lazily. Never logs the key."""
        if self._client is None:
            import openai

            self._client = openai.AsyncOpenAI(
                api_key=self._api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=0,  # we handle retries ourselves
            )
        return self._client

    # ------------------------------------------------------------------ #
    # LLMProvider interface                                              #
    # ------------------------------------------------------------------ #

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        context: RunContext | None = None,
    ) -> AgentResponse:
        """Send a chat completion request to NVIDIA NIM.

        Uses ``stream=False`` by default (non-streaming). If the
        provider was constructed with ``stream=True``, the response is
        aggregated from the stream into a single AgentResponse.

        Retries transient errors (5xx, timeout, rate limit) with
        exponential backoff. Auth errors (401/403) and validation
        errors (400) are NOT retried.
        """
        client = self._ensure_client()
        t0 = time.monotonic()

        from albra_seed.providers.message_serialization import (
            serialize_openai_compatible_messages,
        )

        openai_messages = serialize_openai_compatible_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": openai_messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "top_p": self.top_p,
            "stream": self.stream,
        }
        if tools:
            kwargs["tools"] = tools

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                if self.stream:
                    content = await self._complete_streaming(client, kwargs)
                    tool_calls: list = []
                    raw_usage: Any = None
                else:
                    response = await client.chat.completions.create(**kwargs)
                    choice = response.choices[0]
                    content = choice.message.content or ""
                    tool_calls = self._extract_tool_calls(choice)
                    raw_usage = getattr(response, "usage", None)

                latency_ms = (time.monotonic() - t0) * 1000.0
                execution_id = (
                    context.execution_id if context else new_id("exec")
                )
                correlation_id = (
                    context.correlation_id if context else new_id("corr")
                )

                return AgentResponse(
                    content=content,
                    tool_calls=tool_calls,
                    execution_id=execution_id,
                    correlation_id=correlation_id,
                    confidence=0.8,
                    metadata={
                        "provider": self.name,
                        "model": model or self.model,
                        "latency_ms": round(latency_ms, 2),
                        "usage": raw_usage and raw_usage.model_dump(),
                        "attempt": attempt + 1,
                    },
                )
            except Exception as exc:
                last_exc = exc
                if _is_auth_error(exc):
                    # Auth error — never retry, never leak the key.
                    return self._error_response(exc, context)
                if not _is_transient_error(exc):
                    # Non-transient — fail immediately.
                    return self._error_response(exc, context)
                # Transient — retry with backoff (unless last attempt).
                if attempt < self.max_retries:
                    backoff = 0.5 * (2 ** attempt)
                    await asyncio.sleep(backoff)
                    continue
                # Exhausted retries — return error.
                return self._error_response(exc, context)

        # Should not reach here, but just in case:
        if last_exc is not None:
            return self._error_response(last_exc, context)
        return self._error_response(RuntimeError("unknown nvidia error"), context)

    async def _complete_streaming(
        self, client: Any, kwargs: dict[str, Any]
    ) -> str:
        """Aggregate a streaming response into a single content string.

        NVIDIA NIM supports SSE streaming. We collect ``delta.content``
        chunks and concatenate them. Tool calls in streaming mode are
        NOT supported by this adapter — callers that need tool calls
        should use ``stream=False``.
        """
        chunks: list[str] = []
        # Remove the ``stream`` key from kwargs because it's already
        # implied by the streaming call.
        stream_kwargs = {k: v for k, v in kwargs.items() if k != "stream"}
        stream = await client.chat.completions.create(
            **stream_kwargs, stream=True
        )
        async for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                chunks.append(content)
        return "".join(chunks)

    def _extract_tool_calls(self, choice: Any) -> list:
        """Map OpenAI tool_calls to ToolCall shape if present."""
        tool_calls: list = []
        if getattr(choice.message, "tool_calls", None):
            from albra_seed.core.types import ToolCall
            import json as _json

            for tc in choice.message.tool_calls:
                args: dict[str, Any] = {}
                try:
                    args = _json.loads(tc.function.arguments or "{}")
                except Exception:  # noqa: BLE001
                    args = {}
                tool_calls.append(
                    ToolCall(
                        id=tc.id, tool_name=tc.function.name, arguments=args
                    )
                )
        return tool_calls

    def _error_response(
        self, exc: Exception, context: RunContext | None
    ) -> AgentResponse:
        """Build an error response. NEVER leaks the API key."""
        execution_id = context.execution_id if context else new_id("exec")
        correlation_id = context.correlation_id if context else new_id("corr")
        return AgentResponse(
            content="",
            role=Role.ASSISTANT,
            execution_id=execution_id,
            correlation_id=correlation_id,
            errors=[_safe_error_message(exc)],
            metadata={
                "provider": self.name,
                "failed": True,
                "error_type": type(exc).__name__,
            },
        )

    async def health(self) -> bool:
        """Probe health by making a minimal chat completion request.

        We do NOT call ``models.list()`` because NVIDIA NIM does not
        reliably support it. Instead we send a 1-token completion and
        check for a 200 response. Any error → False.
        """
        try:
            client = self._ensure_client()
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
                stream=False,
            )
            return bool(response.choices)
        except Exception:  # noqa: BLE001
            return False


__all__ = ["NVIDIAProvider"]
