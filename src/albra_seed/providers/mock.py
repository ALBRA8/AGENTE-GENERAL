"""Mock provider — the always-available, zero-dependency LLM provider.

The Mock provider is critical: it allows the SEED to run in CORE_ONLY
mode (no external credentials) end-to-end. It produces deterministic,
content-preserving responses — never an LLM hallucination. This makes
agent execution reproducible and unit-testable.

This file MUST NOT import any optional dependency (openai, anthropic,
etc.). It depends only on the core types.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from albra_seed.core.base import LLMProvider
from albra_seed.core.types import (
    AgentResponse,
    Message,
    RunContext,
    Role,
    new_id,
)

DEFAULT_MOCK_MODEL = "albra-mock-v1"


class MockProvider(LLMProvider):
    """Deterministic mock LLM provider.

    Behaviour
    ---------
    * `is_mock = True`, `is_external = False`.
    * `health()` always returns True.
    * `complete()` echoes the last user message back, prefixed with a
      fixed token so callers can verify they got a mock response.
    * If no user message is present, returns a canonical placeholder.
    * Latency is simulated to be near-zero (a small `time.sleep` is
      applied only if `simulate_latency_ms > 0`).

    The provider is fully deterministic given the input messages — no
    randomness, no external API. This is intentional: reproducibility
    is the feature.
    """

    name: str = "mock"
    is_mock: bool = True
    is_external: bool = False

    def __init__(
        self,
        *,
        model: str = DEFAULT_MOCK_MODEL,
        prefix: str = "[mock]",
        simulate_latency_ms: float = 0.0,
        default_response: str | None = None,
    ) -> None:
        self.model: str = model
        self.prefix: str = prefix
        self.simulate_latency_ms: float = simulate_latency_ms
        self.default_response: str | None = default_response
        self._call_count: int = 0
        # Hash of the last input — used for assertions in tests.
        self._last_input_hash: str | None = None

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
        """Produce a deterministic AgentResponse from the input messages."""
        if self.simulate_latency_ms > 0:
            time.sleep(self.simulate_latency_ms / 1000.0)

        self._call_count += 1
        self._last_input_hash = self._hash_messages(messages)

        # Find the most recent user message — the canonical signal.
        user_text = ""
        for msg in reversed(messages):
            if msg.role == Role.USER:
                user_text = msg.content
                break

        if not user_text and self.default_response is not None:
            content = f"{self.prefix} {self.default_response}"
        elif not user_text:
            content = f"{self.prefix} no-input"
        else:
            content = f"{self.prefix} {user_text}"

        execution_id = context.execution_id if context else new_id("exec")
        correlation_id = context.correlation_id if context else new_id("corr")

        return AgentResponse(
            content=content,
            execution_id=execution_id,
            correlation_id=correlation_id,
            confidence=1.0,
            metadata={
                "provider": self.name,
                "model": model or self.model,
                "mock": True,
                "call_count": self._call_count,
                "input_hash": self._last_input_hash,
                "tools_offered": len(tools) if tools else 0,
            },
        )

    async def health(self) -> bool:
        """Mock is always healthy."""
        return True

    # ------------------------------------------------------------------ #
    # Inspection helpers (used by tests and the Doctor)                  #
    # ------------------------------------------------------------------ #

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def last_input_hash(self) -> str | None:
        return self._last_input_hash

    def describe(self) -> dict[str, Any]:
        """Return a static description suitable for the Doctor."""
        return {
            "name": self.name,
            "is_mock": self.is_mock,
            "is_external": self.is_external,
            "model": self.model,
            "always_available": True,
            "external_dependencies": [],
        }

    # ------------------------------------------------------------------ #
    # Internal                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _hash_messages(messages: list[Message]) -> str:
        h = hashlib.sha256()
        for msg in messages:
            h.update(msg.role.value.encode())
            h.update(b"\x00")
            h.update(msg.content.encode())
            h.update(b"\x00")
        return h.hexdigest()[:16]


__all__ = ["MockProvider", "DEFAULT_MOCK_MODEL"]
