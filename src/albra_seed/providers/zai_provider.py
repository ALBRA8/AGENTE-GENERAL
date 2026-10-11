"""ZAI provider — optional, lazily imported.

ZAI is exposed via the z-ai-web-dev-sdk in JS environments. In Python
we treat it as an OpenAI-compatible endpoint: the user supplies a
`base_url` and `api_key`. The provider will use the `openai` SDK pointed
at that URL.
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

DEFAULT_ZAI_BASE_URL = "https://api.z.ai/api/paas/v4"


class ZAIProvider(LLMProvider):
    """LLM provider backed by ZAI's OpenAI-compatible endpoint."""

    name: str = "zai"
    is_mock: bool = False
    is_external: bool = True

    DEFAULT_MODEL = "glm-4.6"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        try:
            import openai  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise NotImplementedError(
                "zai provider requires the 'openai' package; "
                "install with `pip install albra-seed[openai]`"
            ) from exc

        import os

        resolved_key = api_key or os.environ.get("ZAI_API_KEY")
        if not resolved_key:
            raise NotImplementedError("zai provider requires ZAI_API_KEY")

        self._api_key: str = resolved_key
        self.model: str = model or self.DEFAULT_MODEL
        self.base_url: str = base_url or DEFAULT_ZAI_BASE_URL
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
            confidence=0.8,
            metadata={
                "provider": self.name,
                "model": model or self.model,
                "latency_ms": round(latency_ms, 2),
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
            # Many OpenAI-compatible endpoints reject /models; use a tiny ping.
            await client.chat.completions.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception:
            return False


__all__ = ["ZAIProvider"]
