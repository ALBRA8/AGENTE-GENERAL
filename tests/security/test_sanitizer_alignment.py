"""Consistency tests: detection ↔ classification ↔ redaction alignment.

Verifies that _detect_secrets(), assess_suspicion(), and redact_secrets()
never diverge. If redact_secrets() changes content, assess_suspicion()
must NOT return CLEAN.

Also covers edge cases: quoted values, brackets, short legitimate values,
CVV/PIN, and values with punctuation.
"""
from __future__ import annotations

import pytest
from albra_seed.memory.sanitizer import (
    assess_suspicion, redact_secrets, _detect_secrets, SuspicionLevel,
)


class TestDetectionRedactionConsistency:
    """If redact_secrets changes content, assess_suspicion must NOT be CLEAN."""

    @pytest.mark.parametrize("content", [
        # Inline secrets (various keys + value lengths)
        "password=verysecretpassword123",
        "api_key=sk-1234567890abcdef1234567890abcdef",
        "token=deadbeefdeadbeefdeadbeefdeadbeef",
        "secret=ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
        "cvv=123",                # short-code key
        "pin=4567",               # short-code key
        "ssn=123456789",          # short-code key
        "api_key=abc123de",       # short mixed-alphanumeric
        "password=xyz789",        # short mixed-alphanumeric
        # Standalone secrets
        "The key is sk-1234567890abcdef1234567890abcdef",
        "Bearer abcdefghijklmnopqrstuvwxyz0123456789",
        "deadbeefdeadbeefdeadbeefdeadbeef",
        # Quoted/bracketed values
        '"api_key":"sk-1234567890abcdef1234567890abcdef"',
        "[password=verysecretpassword123]",
        "{token:deadbeefdeadbeefdeadbeefdeadbeef}",
        # Punctuation around values
        "api_key=sk-1234567890abcdef1234567890abcdef.",
        "password=verysecretpassword123;",
        "token=deadbeefdeadbeefdeadbeefdeadbeef)",
    ])
    def test_redacted_implies_suspicious(self, content):
        """If redact_secrets changes content, assess_suspicion != CLEAN."""
        redacted = redact_secrets(content)
        if redacted != content:
            suspicion = assess_suspicion(content)
            assert suspicion != SuspicionLevel.CLEAN, (
                f"DIVERGENCE: redact_secrets changed content but "
                f"assess_suspicion returned {suspicion} for: {content!r}\n"
                f"Redacted: {redacted!r}"
            )

    @pytest.mark.parametrize("content", [
        "User likes Python",
        "The weather is nice today",
        "What time is it?",
        "User prefers Python over JavaScript",
        "password=abc",           # too short, not short-code, no mixed-alnum
        "name=John",              # not a secret key
        "title=Hello World",      # not a secret key
        "id=12345",              # pure numeric, not mixed-alnum, not short-code
        "version=1.0.0",         # not a secret
    ])
    def test_clean_not_redacted(self, content):
        """Legitimate content: CLEAN and not redacted."""
        suspicion = assess_suspicion(content)
        redacted = redact_secrets(content)
        assert redacted == content, f"Unexpected redaction of clean content: {content!r}"
        assert suspicion == SuspicionLevel.CLEAN, (
            f"False positive: assess_suspicion returned {suspicion} for: {content!r}"
        )


class TestShortCodeKeys:
    """CVV, PIN, SSN, card_number — always redacted on any match."""

    def test_cvv_redacted_and_suspicious(self):
        content = "cvv=123"
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS

    def test_pin_redacted_and_suspicious(self):
        content = "pin=4567"
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS

    def test_ssn_redacted_and_suspicious(self):
        content = "ssn=123456789"
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS

    def test_card_number_redacted_and_suspicious(self):
        content = "card_number=4111111111111111"
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS

    def test_credit_card_redacted_and_suspicious(self):
        content = "credit_card=4111111111111111"
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS


class TestStandaloneSecrets:
    """Standalone tokens (not in key=value) are detected and redacted."""

    def test_standalone_sk_key(self):
        content = "The API key is sk-1234567890abcdef1234567890abcdef"
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS

    def test_standalone_bearer(self):
        content = "Authorization Bearer abcdefghijklmnopqrstuvwxyz0123456789"
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS

    def test_standalone_hex(self):
        content = "Token deadbeefdeadbeefdeadbeefdeadbeef"
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS

    def test_standalone_in_brackets(self):
        content = "[sk-1234567890abcdef1234567890abcdef]"
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS

    def test_standalone_in_braces(self):
        content = "{sk-1234567890abcdef1234567890abcdef}"
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS

    def test_standalone_in_quotes(self):
        content = '"sk-1234567890abcdef1234567890abcdef"'
        assert redact_secrets(content) != content
        assert assess_suspicion(content) == SuspicionLevel.SUSPICIOUS


class TestInlineNotRedacted:
    """Inline matches that _should_redact_inline says NOT to redact →
    must NOT be classified as SUSPICIOUS and must NOT be redacted."""

    def test_short_non_secret_value_not_redacted(self):
        """password=abc (3 chars, not short-code, no mixed-alnum) → preserved."""
        content = "password=abc"
        assert redact_secrets(content) == content
        assert assess_suspicion(content) == SuspicionLevel.CLEAN

    def test_short_plain_word_not_redacted(self):
        """password=secret (6 chars, no digit) → preserved (not mixed-alnum)."""
        content = "password=secret"
        assert redact_secrets(content) == content
        assert assess_suspicion(content) == SuspicionLevel.CLEAN

    def test_non_secret_key_not_redacted(self):
        """name=John → not a secret key → preserved."""
        content = "name=John"
        assert redact_secrets(content) == content
        assert assess_suspicion(content) == SuspicionLevel.CLEAN


class TestDetectSecretsDirect:
    """Direct tests of _detect_secrets() alignment with redact_secrets()."""

    def test_detect_returns_false_for_clean_content(self):
        assert _detect_secrets("User likes Python") is False

    def test_detect_returns_true_for_inline_secret(self):
        assert _detect_secrets("password=verysecretpassword123") is True

    def test_detect_returns_true_for_standalone_secret(self):
        assert _detect_secrets("sk-1234567890abcdef1234567890abcdef") is True

    def test_detect_returns_false_for_preserved_inline(self):
        """password=abc → _should_redact_inline returns False → detect returns False."""
        assert _detect_secrets("password=abc") is False

    def test_detect_returns_true_for_cvv(self):
        assert _detect_secrets("cvv=123") is True

    def test_detect_returns_false_for_name_john(self):
        assert _detect_secrets("name=John") is False
