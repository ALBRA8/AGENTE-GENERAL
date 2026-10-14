from __future__ import annotations

import pytest

from albra_seed.security.output_redaction import (
    RedactionError,
    assess_suspicion,
    redact_secrets,
)


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        "sk-ant-abcdefghijklmnopqrstuvwxyz123456",
        "gsk_abcdefghijklmnopqrstuvwxyz1234567890",
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "AKIA1234567890ABCDEF",
        "password=super-secret-value-123",
        '"api_key":"json-secret-value-123456"',
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
        "-----BEGIN PRIVATE KEY-----\nprivate-key-material\n-----END PRIVATE KEY-----",
    ],
)
def test_redaction_removes_every_detected_secret(secret: str) -> None:
    assert assess_suspicion(secret)
    redacted = redact_secrets(secret)
    assert "super-secret-value-123" not in redacted
    assert secret not in redacted
    assert assess_suspicion(redacted) == []


def test_redaction_handles_quoted_json_credentials_and_is_idempotent() -> None:
    raw = '{"api_key":"json-secret-value-123456"}'
    redacted = redact_secrets(raw)
    assert "json-secret-value-123456" not in redacted
    assert "[REDACTED_SECRET]" in redacted
    assert redact_secrets(redacted) == redacted
    assert assess_suspicion(redacted) == []


def test_redaction_preserves_safe_text() -> None:
    assert redact_secrets("Hello, this is safe output.") == "Hello, this is safe output."
    assert assess_suspicion("ordinary words") == []


def test_redaction_fails_closed_for_non_text() -> None:
    with pytest.raises(RedactionError):
        redact_secrets(None)  # type: ignore[arg-type]


async def test_agent_response_redacts_provider_output() -> None:
    from types import SimpleNamespace

    from albra_seed.core.types import AgentResponse
    from albra_seed.runtime.agent import Agent

    run_context = SimpleNamespace(errors=[], warnings=[])
    draft = AgentResponse(
        content="Your key is sk-proj-abcdefghijklmnopqrstuvwxyz123456",
        execution_id="exec_test",
        correlation_id="corr_test",
    )
    ctx = {
        "context": run_context,
        "draft_response": draft,
        "EXECUTE": {"results": []},
    }
    result = await Agent._phase_respond(None, None, ctx)  # type: ignore[arg-type]
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz123456" not in result["content"]
    assert "[REDACTED_SECRET]" in result["content"]


async def test_agent_response_fails_closed_if_redactor_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from albra_seed.core.types import AgentResponse
    from albra_seed.runtime.agent import Agent
    import albra_seed.security.output_redaction as redaction

    def broken(_: str) -> str:
        raise RuntimeError("sanitizer unavailable")

    monkeypatch.setattr(redaction, "redact_secrets", broken)
    run_context = SimpleNamespace(errors=[], warnings=[])
    draft = AgentResponse(
        content="sensitive raw provider output",
        execution_id="exec_test",
        correlation_id="corr_test",
    )
    ctx = {
        "context": run_context,
        "draft_response": draft,
        "EXECUTE": {"results": []},
    }
    result = await Agent._phase_respond(None, None, ctx)  # type: ignore[arg-type]
    assert result["content"] == "Response withheld because output security validation failed."
    assert "sensitive raw provider output" not in result["content"]
    assert run_context.errors


async def test_agent_response_redacts_tool_outputs_and_trace_messages() -> None:
    from types import SimpleNamespace

    from albra_seed.core.types import AgentResponse, ToolResult
    from albra_seed.runtime.agent import Agent

    secret = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    run_context = SimpleNamespace(errors=[f"provider error: {secret}"], warnings=[])
    draft = AgentResponse(
        content="Completed safely.",
        execution_id="exec_test",
        correlation_id="corr_test",
    )
    tool_result = ToolResult(
        call_id="call_test",
        success=True,
        output={"nested": [f"tool returned {secret}"], secret: "secret used as a mapping key"}
    )
    ctx = {
        "context": run_context,
        "draft_response": draft,
        "EXECUTE": {"results": [tool_result]},
    }
    result = await Agent._phase_respond(None, None, ctx)  # type: ignore[arg-type]
    assert secret not in str(result["tool_results"][0].output)
    assert "[REDACTED_SECRET]" in str(result["tool_results"][0].output)
    assert secret not in " ".join(run_context.errors)
    assert "[REDACTED_SECRET]" in " ".join(run_context.errors)


async def test_agent_response_redacts_tool_call_arguments() -> None:
    from types import SimpleNamespace

    from albra_seed.core.types import AgentResponse, ToolCall
    from albra_seed.runtime.agent import Agent

    secret = "gsk_abcdefghijklmnopqrstuvwxyz1234567890"
    run_context = SimpleNamespace(errors=[], warnings=[])
    draft = AgentResponse(
        content="Completed safely.",
        execution_id="exec_test",
        correlation_id="corr_test",
    )
    call = ToolCall(
        id="call_test",
        tool_name="tool.example",
        arguments={"api_key": secret, "nested": {"note": f"token={secret}"}},
    )
    plan = {"tool_calls": [call]}
    ctx = {
        "context": run_context,
        "draft_response": draft,
        "EXECUTE": {"results": []},
        "PLAN": plan,
    }
    result = await Agent._phase_respond(None, None, ctx)  # type: ignore[arg-type]
    assert secret not in str(plan["tool_calls"][0].arguments)
    assert "[REDACTED_SECRET]" in str(plan["tool_calls"][0].arguments)
    assert result["content"] == "Completed safely."


async def test_agent_response_fail_closed_clears_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from albra_seed.core.types import AgentResponse, ToolCall
    from albra_seed.runtime.agent import Agent
    import albra_seed.security.output_redaction as redaction

    monkeypatch.setattr(
        redaction,
        "redact_secrets",
        lambda _: (_ for _ in ()).throw(RuntimeError("sanitizer unavailable")),
    )
    run_context = SimpleNamespace(errors=[], warnings=[])
    draft = AgentResponse(
        content="raw provider response",
        execution_id="exec_test",
        correlation_id="corr_test",
    )
    plan = {"tool_calls": [ToolCall(id="call_test", tool_name="tool.example", arguments={"x": "y"})]}
    ctx = {
        "context": run_context,
        "draft_response": draft,
        "EXECUTE": {"results": []},
        "PLAN": plan,
    }
    result = await Agent._phase_respond(None, None, ctx)  # type: ignore[arg-type]
    assert result["content"] == "Response withheld because output security validation failed."
    assert plan["tool_calls"] == []



def test_build_response_withholds_raw_draft_when_respond_phase_is_missing() -> None:
    from albra_seed.core.identity import AgentIdentity
    from albra_seed.core.types import AgentResponse, RunContext, Session, ToolCall
    from albra_seed.runtime.agent import Agent
    from albra_seed.runtime.runtime import Runtime

    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    context = RunContext(agent_id="agent_test", session_id="session_test", task="test")
    session = Session(agent_id="agent_test")
    draft = AgentResponse(
        content=f"raw provider output {secret}",
        execution_id=context.execution_id,
        correlation_id=context.correlation_id,
    )
    agent = Agent(AgentIdentity(name="redaction-test-agent"), Runtime())
    call = ToolCall(id="call_test", tool_name="tool.example", arguments={"api_key": secret})
    result = agent._build_response(
        context, session, {"draft_response": draft, "PLAN": {"tool_calls": [call]}}, []
    )
    assert secret not in result.content
    assert result.tool_calls == []
    assert result.content == "Response withheld because output security validation did not run."
    assert any("response security phase missing" in error for error in result.errors)
