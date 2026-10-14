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
