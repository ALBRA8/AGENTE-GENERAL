"""Last-mile secret detection and redaction for user-visible model output.

Detection and suspicion share one registry: every recognized pattern is
redacted by the same pattern that classifies it. Keep provider-specific token
formats here so new adapters cannot silently bypass the output boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretPattern:
    name: str
    regex: re.Pattern[str]


# This is the single source of truth for both assessment and redaction.
_SECRET_PATTERNS = (
    SecretPattern(
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
            r"[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----",
            re.IGNORECASE,
        ),
    ),
    SecretPattern("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")),
    SecretPattern(
        "openai_key",
        re.compile(r"\bsk-(?!ant-)[A-Za-z0-9_-]{20,}\b"),
    ),
    SecretPattern("groq_key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    SecretPattern("xai_key", re.compile(r"\bxai-[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("perplexity_key", re.compile(r"\bpplx-[A-Za-z0-9_-]{20,}\b")),
    SecretPattern("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    SecretPattern(
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    SecretPattern("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    SecretPattern("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    SecretPattern("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    SecretPattern(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    SecretPattern(
        "bearer_token",
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    ),
    # Handles plain assignments and quoted JSON/YAML-like fields, e.g.
    # api_key=abc, "api_key":"abc", or Authorization: Bearer ...
    # Credential-labelled values are redacted even when short: length is not
    # a reliable indicator of secrecy, and short test/dev credentials exist.
    SecretPattern(
        "credential_assignment",
        re.compile(
            r"""(?i)(["']?(?:api[_-]?(?:key|token|secret)|access[_-]?token|refresh[_-]?token|id[_-]?token|session[_-]?token|token|password|passwd|secret|client[_-]?secret|secret[_-]?key|private[_-]?key|authorization|credential|bearer)["']?\s*[:=]\s*)(["']?)(?!\[REDACTED_SECRET\])([^\s,;'"<>}]+)(["']?)"""
        ),
    ),
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
            if pattern.name == "credential_assignment":
                result = pattern.regex.sub(
                    lambda match: (
                        match.group(1) + match.group(2) + "[REDACTED_SECRET]" + match.group(4)
                    ),
                    result,
                )
            elif pattern.name == "bearer_token":
                result = pattern.regex.sub("Bearer [REDACTED_SECRET]", result)
            else:
                result = pattern.regex.sub("[REDACTED_SECRET]", result)
        # Invariant: no registered secret pattern may survive redaction.
        if assess_suspicion(result):
            raise RedactionError("secret pattern remained after redaction")
        return result
    except RedactionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RedactionError("secret redaction failed") from exc


__all__ = ["RedactionError", "assess_suspicion", "redact_secrets"]
