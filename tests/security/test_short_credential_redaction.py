from __future__ import annotations

import pytest

from albra_seed.security.output_redaction import assess_suspicion, redact_secrets


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("token=x", "x"),
        ("password=abc", "abc"),
        ('{"api_key":"k1"}', "k1"),
        ("Authorization: Bearer a", "a"),
        ('{"nested":{"client_secret":"dev"}}', "dev"),
    ],
)
def test_redacts_short_credential_assignments(raw: str, secret: str) -> None:
    assert "credential_assignment" in assess_suspicion(raw)
    result = redact_secrets(raw)
    assert secret not in result
    assert "[REDACTED_SECRET]" in result
    assert assess_suspicion(result) == []
    assert redact_secrets(result) == result


def test_short_credential_redaction_preserves_unlabelled_safe_text() -> None:
    text = "The token count is 3 and password reset is available."
    assert redact_secrets(text) == text
