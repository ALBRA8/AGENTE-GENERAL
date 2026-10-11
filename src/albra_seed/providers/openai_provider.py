"""OpenAI provider — optional, lazily imported.

This module implements the `LLMProvider` interface using the `openai`
SDK. The SDK is imported lazily inside `__init__` so that importing this
module does NOT require `openai` to be installed. The provider only
fails if you actually try to instantiate it without the SDK or without
an API key.

This file MUST contain ZERO domain knowledge.
"""

from __future__ import annotations

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


class OpenAIProvider(LLMProvider):
    """LLM provider backed by the OpenAI API."""

    name: str = "openai"
    is_mock: bool = False
    is_external: bool = True

    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        organization: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        try:
            import openai  # noqa: F401  (lazy import)
        except ImportError as exc:  # pragma: no cover
            raise NotImplementedError(
                "openai provider requires the 'openai' package; "
                "install with `pip install albra-seed[openai]`"
            ) from exc

        import os

        resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not resolved_key:
            raise NotImplementedError(
                "openai provider requires OPENAI_API_KEY "
                "(pass api_key= or set the environment variable)"
            )

        self._api_key: str = resolved_key
        self.model: str = model or self.DEFAULT_MODEL
        self.base_url: str | None = base_url
        self.organization: str | None = organization
        self.timeout: float = timeout
        self._client = None  # built lazily on first call

    def _ensure_client(self) -> Any:
        if self._client is None:
            import openai

            self._client = openai.AsyncOpenAI(
                api_key=self._api_key,
                base_url=self.base_url,
                organization=self.organization,
                timeout=self.timeout,
            )
        return self._client

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        context: RunContext | None = None,
    ) -> AgentResponse:
        """Send a chat completion request to OpenAI."""
        client = self._ensure_client()
        t0 = time.monotonic()

        openai_messages = [
            {"role": msg.role.value, "content": msg.content} for msg in messages
        ]
        # If multiple system messages exist, merge them.
        # OpenAI prefers a single system message at the head.

        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            return self._error_response(exc, context)

        latency_ms = (time.monotonic() - t0) * 1000.0
        choice = response.choices[0]
        content = choice.message.content or ""

        tool_calls: list = []
        # Map OpenAI tool_calls to ToolCall shape if present.
        if getattr(choice.message, "tool_calls", None):
            from albra_seed.core.types import ToolCall

            for tc in choice.message.tool_calls:
                import json as _json

                args: dict[str, Any] = {}
                try:
                    args = _json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                tool_calls.append(
                    ToolCall(id=tc.id, tool_name=tc.function.name, arguments=args)
                )

        execution_id = context.execution_id if context else new_id("exec")
        correlation_id = context.correlation_id if context else new_id("corr")

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
                "usage": getattr(response, "usage", None)
                and response.usage.model_dump(),
            },
        )

    def _error_response(self, exc: Exception, context: RunContext | None) -> AgentResponse:
        execution_id = context.execution_id if context else new_id("exec")
        correlation_id = context.correlation_id if context else new_id("corr")
        return AgentResponse(
            content="",
            role=Role.ASSISTANT,
            execution_id=execution_id,
            correlation_id=correlation_id,
            errors=[f"{type(exc).__name__}: {exc}"],
            metadata={"provider": self.name, "failed": True},
        )

    async def health(self) -> bool:
        """Probe health by listing models. Returns False on error."""
        try:
            client = self._ensure_client()
            await client.models.list()
            return True
        except Exception:
            return False


__all__ = ["OpenAIProvider"]
