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
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        "AKIA1234567890ABCDEF",
        "password=super-secret-value-123",
        "-----BEGIN PRIVATE KEY-----\nprivate-key-material\n-----END PRIVATE KEY-----",
    ],
)
def test_redaction_removes_every_detected_secret(secret: str) -> None:
    assert assess_suspicion(secret)
    redacted = redact_secrets(secret)
    assert "super-secret-value-123" not in redacted
    assert secret not in redacted
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
