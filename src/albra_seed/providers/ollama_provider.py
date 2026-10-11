"""Ollama provider — optional, lazily imported.

Ollama runs locally and exposes an OpenAI-compatible API at
`http://localhost:11434/v1` by default. We use the `openai` SDK pointed
at that base URL so the implementation is close to OpenAI's.
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

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"


class OllamaProvider(LLMProvider):
    """LLM provider backed by a local Ollama server (OpenAI-compatible)."""

    name: str = "ollama"
    is_mock: bool = False
    is_external: bool = True  # local but still external to the SEED process

    DEFAULT_MODEL = "llama3.1"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str = "ollama",  # ollama ignores this but SDK requires non-empty
        timeout: float = 60.0,
    ) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise NotImplementedError(
                "ollama provider requires the 'openai' package; "
                "install with `pip install albra-seed[openai]`"
            ) from exc

        import os

        resolved_url = base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
        if not resolved_url:
            raise NotImplementedError("ollama provider requires OLLAMA_BASE_URL")

        self.base_url: str = resolved_url
        self.model: str = model or self.DEFAULT_MODEL
        self._api_key: str = api_key
        self.timeout: float = timeout
        self._client = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self.base_url,
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
        client = self._ensure_client()
        t0 = time.monotonic()

        chat_messages = [
            {"role": msg.role.value, "content": msg.content} for msg in messages
        ]
        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": chat_messages,
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

        execution_id = context.execution_id if context else new_id("exec")
        correlation_id = context.correlation_id if context else new_id("corr")
        return AgentResponse(
            content=content,
            execution_id=execution_id,
            correlation_id=correlation_id,
            confidence=0.7,
            metadata={
                "provider": self.name,
                "model": model or self.model,
                "latency_ms": round(latency_ms, 2),
                "local": True,
            },
        )

    def _error_response(
        self, exc: Exception, context: RunContext | None
    ) -> AgentResponse:
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
        try:
            client = self._ensure_client()
            await client.models.list()
            return True
        except Exception:
            return False


__all__ = ["OllamaProvider"]
