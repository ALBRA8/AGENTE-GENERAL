"""Tests for canonical HMAC representation and secret detection alignment.

Hallazgo 1: Canonical representation must prevent separator collisions.
Hallazgo 2: assess_suspicion() and redact_secrets() must use the same detection.
"""
from __future__ import annotations

import dataclasses
import pytest

from albra_seed.security.auth import AuthPrincipal
from albra_seed.security.trusted_principal import (
    TrustedPrincipal, TrustedPrincipalIssuer, TrustedPrincipalVerifier,
)
from albra_seed.memory.sanitizer import (
    assess_suspicion, redact_secrets, sanitize_for_context,
    SuspicionLevel, _detect_secrets,
)


# =====================================================================
# Hallazgo 1: Canonical representation — no separator collisions
# =====================================================================


class TestCanonicalRepresentation:
    """Different attribute sets must produce different seals."""

    def test_roles_with_comma_in_value_not_confused_with_multiple_roles(self):
        """roles=("admin,foo",) must NOT produce same seal as roles=("admin","foo")."""
        issuer = TrustedPrincipalIssuer()
        p1 = AuthPrincipal(identity="alice", is_anonymous=False, roles=["admin,foo"])
        p2 = AuthPrincipal(identity="alice", is_anonymous=False, roles=["admin", "foo"])
        t1 = issuer.seal(p1)
        t2 = issuer.seal(p2)
        # Seals must differ — the canonical representation distinguishes them
        assert t1.seal != t2.seal, "Canonical collision: roles with commas not distinguished!"

    def test_principal_id_with_pipe_not_confused_with_field_boundary(self):
        """principal_id="a|b" must NOT collide with principal_id="a", kind="b"."""
        issuer = TrustedPrincipalIssuer()
        # Create principals with different attrs that would collide with | separator
        p1 = AuthPrincipal(identity="a|b", is_anonymous=False, kind="user", roles=[])
        p2 = AuthPrincipal(identity="a", is_anonymous=False, kind="b", roles=[])
        t1 = issuer.seal(p1)
        t2 = issuer.seal(p2)
        assert t1.seal != t2.seal, "Canonical collision: pipe in principal_id!"

    def test_kind_with_pipe_not_confused(self):
        """kind="x|y" must NOT collide with kind="x", auth_method="y"."""
        issuer = TrustedPrincipalIssuer()
        p1 = AuthPrincipal(identity="alice", is_anonymous=False, kind="x|y", auth_method="jwt", roles=[])
        p2 = AuthPrincipal(identity="alice", is_anonymous=False, kind="x", auth_method="y", roles=[])
        t1 = issuer.seal(p1)
        t2 = issuer.seal(p2)
        assert t1.seal != t2.seal

    def test_altered_principal_id_invalidates_seal(self):
        """Altering principal_id after sealing → seal verification fails."""
        issuer = TrustedPrincipalIssuer()
        real = issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))
        altered = dataclasses.replace(real, principal_id="admin")
        assert not issuer.verify(altered)

    def test_altered_roles_invalidates_seal(self):
        """Adding a role after sealing → seal verification fails."""
        issuer = TrustedPrincipalIssuer()
        real = issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False, roles=[]))
        altered = dataclasses.replace(real, roles=("admin",))
        assert not issuer.verify(altered)

    def test_altered_kind_invalidates_seal(self):
        """Changing kind after sealing → seal verification fails."""
        issuer = TrustedPrincipalIssuer()
        real = issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False, kind="user"))
        altered = dataclasses.replace(real, kind="service")
        assert not issuer.verify(altered)

    def test_altered_is_anonymous_invalidates_seal(self):
        """Flipping is_anonymous after sealing → seal verification fails."""
        issuer = TrustedPrincipalIssuer()
        real = issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False))
        altered = dataclasses.replace(real, is_anonymous=True)
        assert not issuer.verify(altered)

    def test_legitimate_seal_still_valid(self):
        """A principal sealed by the correct issuer still verifies."""
        issuer = TrustedPrincipalIssuer()
        sealed = issuer.seal(AuthPrincipal(identity="alice", is_anonymous=False, roles=["admin"]))
        assert issuer.verify(sealed)

    def test_empty_roles_vs_none_roles(self):
        """roles=[] is the default; None is rejected by pydantic. Verify [] works."""
        issuer = TrustedPrincipalIssuer()
        p1 = AuthPrincipal(identity="alice", is_anonymous=False, roles=[])
        t1 = issuer.seal(p1)
        assert issuer.verify(t1)


# =====================================================================
# Hallazgo 2: assess_suspicion() and redact_secrets() alignment
# =====================================================================


class TestSecretDetectionAlignment:
    """assess_suspicion() and redact_secrets() must agree on what's a secret."""

    def test_standalone_sk_key_detected_and_redacted(self):
        """Standalone sk- key: detected by assess_suspicion AND redacted."""
        content = "The key is sk-1234567890abcdef1234567890abcdef"
        suspicion = assess_suspicion(content)
        redacted = redact_secrets(content)
        assert suspicion == SuspicionLevel.SUSPICIOUS, f"Expected SUSPICIOUS, got {suspicion}"
        assert "sk-1234567890abcdef1234567890abcdef" not in redacted

    def test_standalone_bearer_token_detected_and_redacted(self):
        """Standalone Bearer token: detected AND redacted."""
        content = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789"
        suspicion = assess_suspicion(content)
        redacted = redact_secrets(content)
        assert suspicion == SuspicionLevel.SUSPICIOUS
        assert "Bearer abcdefghijklmnopqrstuvwxyz0123456789" not in redacted

    def test_standalone_hex_detected_and_redacted(self):
        """Standalone hex string >= 32: detected AND redacted."""
        content = "Token: deadbeefdeadbeefdeadbeefdeadbeef"
        suspicion = assess_suspicion(content)
        redacted = redact_secrets(content)
        assert suspicion == SuspicionLevel.SUSPICIOUS
        assert "deadbeefdeadbeefdeadbeefdeadbeef" not in redacted

    def test_inline_password_detected_and_redacted(self):
        """Inline password=key: detected AND redacted."""
        content = "user password=verysecretpassword123"
        suspicion = assess_suspicion(content)
        redacted = redact_secrets(content)
        assert suspicion == SuspicionLevel.SUSPICIOUS
        assert "verysecretpassword123" not in redacted

    def test_legitimate_content_not_suspicious_not_redacted(self):
        """Normal content: CLEAN and not redacted."""
        content = "User prefers Python over JavaScript"
        suspicion = assess_suspicion(content)
        redacted = redact_secrets(content)
        assert suspicion == SuspicionLevel.CLEAN
        assert redacted == content

    def test_no_divergence_inline_secret(self):
        """If redact_secrets changes content, assess_suspicion must be SUSPICIOUS."""
        test_cases = [
            "password=verysecretpassword123",
            "api_key=sk-1234567890abcdef1234567890abcdef",
            "token=deadbeefdeadbeefdeadbeefdeadbeef",
            "secret=ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
        ]
        for content in test_cases:
            redacted = redact_secrets(content)
            suspicion = assess_suspicion(content)
            # If content was changed by redaction, suspicion must NOT be CLEAN
            if redacted != content:
                assert suspicion != SuspicionLevel.CLEAN, (
                    f"DIVERGENCE: redact_secrets changed content but "
                    f"assess_suspicion returned {suspicion} for: {content!r}"
                )

    def test_no_divergence_standalone_secret(self):
        """If a standalone secret is redacted, assess_suspicion must be SUSPICIOUS."""
        test_cases = [
            "The key is sk-1234567890abcdef1234567890abcdef",
            "Bearer abcdefghijklmnopqrstuvwxyz0123456789",
            "deadbeefdeadbeefdeadbeefdeadbeef",
        ]
        for content in test_cases:
            redacted = redact_secrets(content)
            suspicion = assess_suspicion(content)
            if redacted != content:
                assert suspicion == SuspicionLevel.SUSPICIOUS, (
                    f"DIVERGENCE: standalone secret redacted but "
                    f"assess_suspicion returned {suspicion} for: {content!r}"
                )

    def test_detect_secrets_matches_redact_secrets(self):
        """_detect_secrets() returns True for exactly the content that redact_secrets changes."""
        secret_contents = [
            "password=verysecretpassword123",
            "sk-1234567890abcdef1234567890abcdef",
            "Bearer abcdefghijklmnopqrstuvwxyz0123456789",
        ]
        clean_contents = [
            "User likes Python",
            "The weather is nice today",
            "What time is it?",
        ]
        for c in secret_contents:
            assert _detect_secrets(c) is True, f"Expected detection for: {c!r}"
        for c in clean_contents:
            assert _detect_secrets(c) is False, f"Unexpected detection for: {c!r}"
