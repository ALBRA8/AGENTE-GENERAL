"""Last-mile secret detection and redaction for user-visible model output.

Detection and suspicion deliberately share the exact same pattern registry so
that any value redacted from output is also classified as suspicious.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretPattern:
    name: str
    regex: re.Pattern[str]


# Keep this registry as the single source of truth for both APIs.
_SECRET_PATTERNS = (
    SecretPattern("private_key", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        re.IGNORECASE,
    )),
    SecretPattern("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    SecretPattern("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    SecretPattern("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    SecretPattern("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    SecretPattern("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    SecretPattern("credential_assignment", re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|client[_-]?secret|secret[_-]?key)\b(\s*[:=]\s*)(['\"]?)[^\s,;'\"}]{8,}\3"
    )),
)


class RedactionError(RuntimeError):
    """Raised when redaction cannot safely complete."""


def assess_suspicion(text: str) -> list[str]:
    """Return unique secret categories found in text."""
    if not isinstance(text, str):
        raise TypeError("secret assessment requires text")
    return [pattern.name for pattern in _SECRET_PATTERNS if pattern.regex.search(text)]


def redact_secrets(text: str) -> str:
    """Redact all registered secret patterns; fail closed on invalid input."""
    if not isinstance(text, str):
        raise RedactionError("output was not text")
    try:
        result = text
        for pattern in _SECRET_PATTERNS:
            result = pattern.regex.sub("[REDACTED_SECRET]", result)
        # Invariant: nothing classified as a secret may survive redaction.
        if assess_suspicion(result):
            raise RedactionError("secret pattern remained after redaction")
        return result
    except RedactionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RedactionError("secret redaction failed") from exc


__all__ = ["RedactionError", "assess_suspicion", "redact_secrets"]
