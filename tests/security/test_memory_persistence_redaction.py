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


class _CaptureSink:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def record(self, payload: Any) -> None:
        self.payloads.append(repr(payload))

    async def append(self, payload: Any) -> None:
        self.payloads.append(repr(payload))


class _RuntimeCapture:
    def __init__(
        self,
        memory: _MemoryCapture,
        evidence: _CaptureSink | None = None,
        audit: _CaptureSink | None = None,
    ) -> None:
        self.memory = memory
        self.evidence = evidence
        self.audit = audit

    def get_component(self, name: str) -> Any:
        if "evidence" in name:
            return self.evidence
        if "audit" in name:
            return self.audit
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
    evidence = _CaptureSink()
    audit = _CaptureSink()
    agent = SimpleNamespace(runtime=_RuntimeCapture(memory, evidence, audit))
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
    assert evidence.payloads and audit.payloads
    assert "SECRET123" not in repr(evidence.payloads)
    assert "SECRET123" not in repr(audit.payloads)
    assert "TASKSECRET123" not in run_ctx.task
    assert "USERSECRET123" not in session.messages[0].content
    assert "ASSISTANTSECRET123" not in ctx["draft_response"].content
    assert isinstance(ctx["PLAN"]["tool_calls"][0], ToolCall)
    assert isinstance(ctx["EXECUTE"]["results"][0], ToolResult)
    assert isinstance(ctx["draft_response"], AgentResponse)


@pytest.mark.asyncio
async def test_record_phase_skips_all_persistence_if_redaction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import albra_seed.security.output_redaction as output_redaction

    memory = _MemoryCapture()
    evidence = _CaptureSink()
    audit = _CaptureSink()
    agent = SimpleNamespace(runtime=_RuntimeCapture(memory, evidence, audit))
    ctx, run_ctx, session = _context_with_secrets()

    def fail_redaction(_: str) -> str:
        raise output_redaction.RedactionError("must not leak details")

    monkeypatch.setattr(output_redaction, "redact_secrets", fail_redaction)

    result = await Agent._phase_record(agent, None, ctx)

    assert result == {"recorded": False, "redaction_failed": True}
    assert memory.records == []
    assert evidence.payloads == []
    assert audit.payloads == []
    assert run_ctx.errors == ["record output redaction failed; persistence skipped"]
    assert "TASKSECRET123" not in repr(run_ctx.errors)
    assert run_ctx.task == "[REDACTED]"
    assert run_ctx.inputs == {}
    assert run_ctx.outputs == {}
    assert session.messages == []
    assert ctx["RECEIVE"] == {"content": "[REDACTED]"}
    assert "TASKSECRET123" not in repr(ctx)



def test_build_response_redacts_exception_text_in_trace() -> None:
    from albra_seed.runtime.loop import PhaseEvent, PhaseStatus

    run_ctx = RunContext(agent_id="agent-test", task="safe task")
    session = Session(agent_id="agent-test")
    agent = SimpleNamespace(identity=SimpleNamespace(agent_id="agent-test"))
    phase = PhaseEvent(
        phase="EXECUTE",
        status=PhaseStatus.FAILURE,
        execution_id=run_ctx.execution_id,
        error="provider failed with password=TRACESECRET123",
    )

    response = Agent._build_response(
        agent,
        run_ctx,
        session,
        {
            "RESPOND": {"content": "safe response", "confidence": 0.8, "tool_results": []},
            "PLAN": {"tool_calls": []},
        },
        [phase],
    )

    assert "TRACESECRET123" not in repr(run_ctx.outputs["trace"])
    assert "[REDACTED_SECRET]" in run_ctx.outputs["trace"][0]["error"]
    assert "TRACESECRET123" not in repr(response)



@pytest.mark.asyncio
async def test_final_response_redacts_short_values_under_secret_mapping_keys() -> None:
    run_ctx = RunContext(agent_id="agent-test", task="safe task")
    draft = AgentResponse(
        content="safe response",
        execution_id=run_ctx.execution_id,
        correlation_id=run_ctx.correlation_id,
    )
    call = ToolCall(
        tool_name="secret_tool",
        arguments={"api_key": "FINALARGSECRET123", "ordinary": "safe"},
    )
    result = ToolResult(
        call_id=call.id,
        success=True,
        output={"client_secret": "FINALOUTPUTSECRET123", "ordinary": "safe"},
    )
    ctx: dict[str, Any] = {
        "context": run_ctx,
        "draft_response": draft,
        "PLAN": {"tool_calls": [call]},
        "EXECUTE": {"results": [result]},
    }

    response = await Agent._phase_respond(SimpleNamespace(), None, ctx)

    assert "FINALARGSECRET123" not in repr(ctx["PLAN"]["tool_calls"])
    assert "FINALOUTPUTSECRET123" not in repr(response["tool_results"])
    assert "safe" in repr(response["tool_results"])



class _FailingEvidence:
    async def record(self, _: Any) -> None:
        raise RuntimeError("provider password=EVIDENCESECRET123")


@pytest.mark.asyncio
async def test_persistence_sink_errors_do_not_leak_into_audit_or_memory() -> None:
    memory = _MemoryCapture()
    evidence = _FailingEvidence()
    audit = _CaptureSink()
    agent = SimpleNamespace(runtime=_RuntimeCapture(memory, evidence, audit))
    ctx, run_ctx, _ = _context_with_secrets()

    await Agent._phase_record(agent, None, ctx)

    assert "EVIDENCESECRET123" not in repr(audit.payloads)
    assert "EVIDENCESECRET123" not in repr(memory.records)
    assert "details withheld" in repr(run_ctx.warnings)
