"""Real LLM provider via z-ai CLI (GLM-4-plus).

This provider calls the z-ai CLI subprocess to get real LLM responses.
Used ONLY in tests/llm_real/ for prompt-injection validation with a
real LLM. NOT registered in the seed's default provider registry.

The provider is DETERMINISTIC in structure (same messages -> same API call)
but the LLM response is non-deterministic (may vary between runs).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from albra_seed.core.base import LLMProvider
from albra_seed.core.types import AgentResponse, Message, Role, RunContext, new_id


class ZaiCliProvider(LLMProvider):
    """Real LLM provider via z-ai CLI subprocess."""

    name: str = "zai-cli"
    is_mock: bool = False
    is_external: bool = True

    def __init__(self, *, model: str = "glm-4-plus", timeout: float = 30.0) -> None:
        self.model = model
        self.timeout = timeout

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
        """Call z-ai CLI with the messages, return AgentResponse."""
        # Build the prompt: concatenate all messages into a single prompt.
        # The z-ai CLI takes --prompt (user message) + --system (system prompt).
        system_parts: list[str] = []
        user_parts: list[str] = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_parts.append(msg.content)
            elif msg.role == Role.USER:
                user_parts.append(msg.content)
            elif msg.role == Role.ASSISTANT:
                user_parts.append(f"[assistant previously said]: {msg.content}")

        system_prompt = (
            "\n\n".join(system_parts) if system_parts else "You are a helpful assistant."
        )
        user_prompt = "\n\n".join(user_parts) if user_parts else ""

        # Write prompt to a temp file to avoid shell escaping issues.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
            f.write(user_prompt)
            prompt_file = f.name

        # Allocate a unique path for the z-ai CLI's JSON output. We open the
        # file briefly just to reserve the path (the CLI writes to it
        # directly). Suppresses ruff's SIM115 (use a context manager) while
        # keeping the delete=False semantics we need (the file must persist
        # beyond the open() call so z-ai can write to it, then we read it
        # back and unlink it in the finally block below).
        with tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="r", suffix=".json", delete=False
        ) as _out_handle:
            output_file = _out_handle.name

        try:
            # Call z-ai CLI.
            cmd = [
                "z-ai",
                "chat",
                "--prompt",
                user_prompt,
                "--system",
                system_prompt,
                "-o",
                output_file,
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )

            execution_id = context.execution_id if context else new_id("exec")
            correlation_id = context.correlation_id if context else new_id("corr")

            if proc.returncode != 0:
                err_tail = (stderr or b"").decode(errors="replace")[:200]
                return AgentResponse(
                    content="",
                    execution_id=execution_id,
                    correlation_id=correlation_id,
                    errors=[f"z-ai CLI failed: {err_tail}"],
                )

            # Parse the JSON output.
            try:
                with open(output_file) as f:
                    result = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                return AgentResponse(
                    content="",
                    execution_id=execution_id,
                    correlation_id=correlation_id,
                    errors=[f"z-ai CLI output parse error: {type(exc).__name__}: {exc}"],
                )

            content = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            return AgentResponse(
                content=content,
                execution_id=execution_id,
                correlation_id=correlation_id,
                confidence=0.7,
                metadata={
                    "provider": self.name,
                    "model": self.model,
                    "tokens": result.get("usage", {}).get("total_tokens", 0),
                },
            )
        except asyncio.TimeoutError:
            execution_id = context.execution_id if context else new_id("exec")
            correlation_id = context.correlation_id if context else new_id("corr")
            return AgentResponse(
                content="",
                execution_id=execution_id,
                correlation_id=correlation_id,
                errors=["z-ai CLI timeout"],
            )
        except Exception as exc:  # noqa: BLE001
            execution_id = context.execution_id if context else new_id("exec")
            correlation_id = context.correlation_id if context else new_id("corr")
            return AgentResponse(
                content="",
                execution_id=execution_id,
                correlation_id=correlation_id,
                errors=[f"z-ai CLI error: {type(exc).__name__}: {exc}"],
            )
        finally:
            Path(prompt_file).unlink(missing_ok=True)
            Path(output_file).unlink(missing_ok=True)

    async def health(self) -> bool:
        """Quick health check via a tiny prompt."""
        try:
            response = await self.complete(
                [Message(role=Role.USER, content="ping")]
            )
            # Health is good only if no errors were recorded.
            return not (response.errors or [])
        except Exception:  # noqa: BLE001
            return False


__all__ = ["ZaiCliProvider"]
