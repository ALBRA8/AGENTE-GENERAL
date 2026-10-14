from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from albra_seed.core.types import (
    AgentResponse,
    Message,
    Role,
    RunContext,
    Session,
    ToolCall,
    ToolResult,
)
from albra_seed.runtime.agent import Agent


class _MemoryCapture:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def remember(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


class _RuntimeCapture:
    def __init__(self, memory: _MemoryCapture) -> None:
        self.memory = memory

    def get_component(self, _: str) -> None:
        return None

    def get_memory_manager(self) -> _MemoryCapture:
        return self.memory

    def get_memory_store(self, _: str) -> None:
        return None


def _context_with_secrets() -> tuple[dict[str, Any], RunContext, Session]:
    run_ctx = RunContext(
        agent_id="agent-test",
        task="token=TASKSECRET123",
        inputs={"api_key": "CTXSECRET123"},
        errors=["password=ERRSECRET123"],
        warnings=["token=WARNSECRET123"],
    )
    message = Message(role=Role.USER, content="token=USERSECRET123")
    session = Session(agent_id="agent-test", messages=[message])
    call = ToolCall(tool_name="secret_tool", arguments={"api_key": "ARGSECRET123"})
    result = ToolResult(
        call_id=call.id,
        success=True,
        output={"client_secret": "OUTSECRET123"},
        error="password=TOOLERRSECRET123",
    )
    draft = AgentResponse(
        content="password=ASSISTANTSECRET123",
        execution_id=run_ctx.execution_id,
        correlation_id=run_ctx.correlation_id,
        metadata={"provider_token": "METADATASECRET123"},
    )
    ctx: dict[str, Any] = {
        "context": run_ctx,
        "session": session,
        "input_message": message,
        "RECEIVE": {"content": "token=USERSECRET123"},
        "PLAN": {"tool_calls": [call]},
        "EXECUTE": {"results": [result]},
        "draft_response": draft,
    }
    return ctx, run_ctx, session


@pytest.mark.asyncio
async def test_record_phase_redacts_secrets_before_memory_persistence() -> None:
    memory = _MemoryCapture()
    agent = SimpleNamespace(runtime=_RuntimeCapture(memory))
    ctx, run_ctx, session = _context_with_secrets()

    result = await Agent._phase_record(agent, None, ctx)

    assert result["recorded"] is True
    assert len(memory.records) == 2
    persisted = repr(memory.records)
    for secret in (
        "TASKSECRET123",
        "CTXSECRET123",
        "USERSECRET123",
        "ARGSECRET123",
        "OUTSECRET123",
        "TOOLERRSECRET123",
        "ASSISTANTSECRET123",
        "METADATASECRET123",
        "ERRSECRET123",
        "WARNSECRET123",
    ):
        assert secret not in persisted

    assert "[REDACTED_SECRET]" in persisted or "<redacted>" in persisted
    assert "TASKSECRET123" not in run_ctx.task
    assert "USERSECRET123" not in session.messages[0].content
    assert "ASSISTANTSECRET123" not in ctx["draft_response"].content


@pytest.mark.asyncio
async def test_record_phase_skips_all_persistence_if_redaction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import albra_seed.security.output_redaction as output_redaction

    memory = _MemoryCapture()
    agent = SimpleNamespace(runtime=_RuntimeCapture(memory))
    ctx, run_ctx, _ = _context_with_secrets()

    def fail_redaction(_: str) -> str:
        raise output_redaction.RedactionError("must not leak details")

    monkeypatch.setattr(output_redaction, "redact_secrets", fail_redaction)

    result = await Agent._phase_record(agent, None, ctx)

    assert result == {"recorded": False, "redaction_failed": True}
    assert memory.records == []
    assert run_ctx.errors == ["record output redaction failed; persistence skipped"]
    assert "TASKSECRET123" not in repr(run_ctx.errors)
