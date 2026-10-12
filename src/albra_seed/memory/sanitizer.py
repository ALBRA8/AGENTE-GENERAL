"""Memory content sanitizer — defense-in-depth for retrieved memories.

This module applies lexical sanitization to memory content BEFORE it is
included in the LLM context. It is NOT the primary security barrier —
the structural perimeter (ActionFirewall, PolicyEngine, ToolExecutor)
is the primary barrier, as those components do NOT read memory content.

This sanitizer provides defense-in-depth by:
1. Detecting and redacting potential secrets (API keys, tokens, passwords)
   so they cannot be leaked via the LLM's text output.
2. Detecting known instruction-injection patterns (e.g. "ignore previous
   instructions", "you are now DAN") and replacing them with a marker.
3. Marking memories as suspicious when they contain injection patterns,
   so callers can exclude or flag them.

KNOWN LIMITATIONS (documented, not exhaustive):
- Lexical patterns can be evaded by paraphrasing, obfuscation, or unicode.
- False positives: legitimate content containing "password" as a topic
  (e.g. "user forgot their password") will be redacted.
- False negatives: novel injection techniques not in the pattern list
  will pass through.
- This is NOT a substitute for the structural perimeter. It is a
  defense-in-depth layer that reduces the probability of LLM text
  following injected instructions.

The patterns are synced with ``observability/logger.py`` secret detection
to avoid divergence. If you update patterns here, update there too.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

# =====================================================================
# Secret detection (synced with observability/logger.py)
# =====================================================================

# Key substrings that indicate a secret value. Kept in sync with
# ``observability/logger.py`` so the logger and the sanitizer flag the
# same set of sensitive fields (defense-in-depth: redact in the
# sanitizer BEFORE the value reaches the logger).
#
# Phase 2.3 audit additions: ``ssn``, ``credit_card``, ``card_number``,
# ``cvv``, ``pin`` — these are short, high-sensitivity credentials
# (CVV/PIN are 3-4 chars by spec; SSN is 9 digits; PANs are 13-19 digits).
# Without them, a memory containing ``cvv=123`` would NOT be flagged.
_SECRET_KEY_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "private_key",
    "session_token",
    "refresh_token",
    "auth",
    "bearer",
    "jwt",
    "cookie",
    "passphrase",
    "ssn",
    "credit_card",
    "card_number",
    "cvv",
    "pin",
)

# Keys whose values are SHORT BY SPECIFICATION (CVV=3-4 digits, PIN=4-6
# digits, SSN=9 digits, PANs=13-19 digits). For these keys, the standard
# "redact only if value >= 16 chars" threshold would NEVER fire — we'd
# leak every CVV/PIN/SSN. So when the matched key is one of these, we
# redact on ANY non-empty match (length >= 1).
_SHORT_CODE_KEYS: frozenset[str] = frozenset(
    {
        "cvv",
        "pin",
        "ssn",
        "card_number",
        "credit_card",
        "creditcard",
        "cardnumber",
    }
)

# Value patterns that look like secrets (long base64-ish / hex strings).
# Matches ``sk-...``, ``Bearer ...``, hex strings >=32 chars, base64 >=32 chars.
_SECRET_VALUE_PATTERN = re.compile(
    r"^(sk-[A-Za-z0-9_\-]{20,}|"
    r"Bearer\s+[A-Za-z0-9_\-\.]{20,}|"
    r"[A-Fa-f0-9]{32,}|"
    r"[A-Za-z0-9+/]{32,}={0,2})$"
)

# Inline secret patterns (within text, not just standalone values).
# Matches ``key=value``, ``key: value``, ``"key": "value"`` (JSON-style),
# or ``key='value'`` patterns. Optional surrounding quotes on both key and
# value let the same pattern cover plain-prose and JSON-serialized dict
# content (which is what ``sanitize_memory_content`` produces for non-str
# inputs).
#
# Phase 2.3 audit additions: ``ssn``, ``credit_card``, ``card_number``,
# ``cvv``, ``pin`` — the same set added to ``_SECRET_KEY_SUBSTRINGS``.
# Keeping the inline pattern in sync with the substring list is the fix
# for the discrepancy between ``assess_suspicion()`` (which consults the
# inline pattern) and ``redact_secrets()`` (which uses the same pattern
# via ``.sub()``). Before this fix, ``assess_suspicion('cvv=123')``
# returned CLEAN because ``cvv`` was not in the inline pattern — even
# though the substring list (used by the logger) already considered ``cvv``
# sensitive. Detection and classification now share one source of truth.
#
# Value capture: ``\S+?`` (1+ non-whitespace, non-greedy). We keep the
# minimum at 1 (not 4 or 8) so short-code keys like ``cvv=123`` (3-char
# value) and ``pin=12`` (2-char value) still match for DETECTION. The
# decision to actually REDACT is delegated to ``_replace_inline``, which
# applies a context-aware threshold (see below).
_INLINE_SECRET_PATTERN = re.compile(
    r'(?i)["\']?(password|passwd|secret|token|credential|api_?key|access_?key|'
    r"private_?key|session_?token|refresh_?token|bearer|jwt|passphrase|"
    r"ssn|credit_?card|card_?number|cvv|pin)['\"]?"
    r"\s*[:=]\s*[\"']?(\S+?)[\"']?(?=\s|$|[}\],])"
)

_REDACTED_SECRET = "[REDACTED: potential secret]"


def _looks_like_secret_value(value: str) -> bool:
    """Check if a standalone string value looks like a secret.

    Mirrors the logic of ``observability/logger.py:_looks_like_secret_value``
    so that a value considered secret by the logger is also considered
    secret here (defense-in-depth: redact before it even reaches the logger).

    Phase 2.3 audit: the legacy threshold of ``len(value) >= 24`` missed
    mid-length standalone credentials (e.g. a 16-char API key fragment).
    We lower the floor to ``>= 16`` so the standalone path catches the same
    set as the inline path's LONG-SECRET branch. Short standalone values
    (< 16 chars) are left to the inline-pattern path (``key=value``) which
    uses a context-aware threshold (see ``_should_redact_inline``).

    Note: this function is for STANDALONE values only — tokens that
    appear on their own (``sk-...``, ``Bearer ...``, a hex string). It is
    NOT responsible for inline ``key=value`` patterns; those are handled
    by ``_INLINE_SECRET_PATTERN`` + ``_should_redact_inline``. Adding an
    inline-pattern check here would cause the standalone path to redact
    tokens that the inline path already preserved (e.g. ``password=secret123``
    when the value ``secret123`` is 9 chars — between the short-code and
    long-secret thresholds), creating a discrepancy between detection and
    redaction.
    """
    if not isinstance(value, str) or len(value) < 16:
        return False
    return bool(_SECRET_VALUE_PATTERN.match(value))


def _is_short_code_key(key: str) -> bool:
    """Return True if the matched key is a short-by-spec credential
    (CVV, PIN, SSN, card number). For these keys we redact on ANY non-
    empty match — the standard 16-char threshold would never fire.
    """
    if not key:
        return False
    k = key.lower().replace("_", "").replace("-", "")
    return k in {
        "cvv",
        "pin",
        "ssn",
        "cardnumber",
        "creditcard",
    }


def _looks_like_short_code(value: str) -> bool:
    """Return True if a value looks like a short alphanumeric code (e.g.
    ``abc123``, ``xyz789``).

    Heuristic: length 4-8 AND contains at least one letter AND at least
    one digit. This catches short mixed-alphanumeric credentials (the
    most common form factor for 2FA codes, short tokens, PINs with
    letters, ...) while leaving plain words (``secret``) and long
    sentences alone.
    """
    if not isinstance(value, str):
        return False
    if not (4 <= len(value) <= 8):
        return False
    has_letter = any(c.isalpha() for c in value)
    has_digit = any(c.isdigit() for c in value)
    return has_letter and has_digit


def _should_redact_inline(key: str, val: str) -> bool:
    """Decide whether to redact an inline ``key=value`` match.

    Three independent triggers (any one is sufficient):

    1. SHORT-CODE KEY: the key is in ``_SHORT_CODE_KEYS`` (cvv, pin, ssn,
       card_number, credit_card) — redact on any non-empty match.
    2. LONG SECRET: value length >= 16 (existing threshold — catches
       real API keys / tokens / hex strings).
    3. SHORT CODE: value is a 4-8 char mixed alphanumeric (e.g. ``abc123``)
       — catches short credentials that don't meet the 16-char bar.
    """
    if not val:
        return False
    if _is_short_code_key(key):
        return True
    if len(val) >= 16:
        return True
    return bool(_looks_like_short_code(val))


def redact_secrets(content: str) -> str:
    """Redact potential secrets from a string.

    Replaces:
    - Inline ``key=value`` / ``key: value`` / ``"key": "value"`` patterns —
      the value part is replaced with the redaction marker; short values
      (<16 chars) are kept (likely not secrets — a real API key / token
      is almost always >= 20 chars).
    - Standalone secret values (``sk-...``, ``Bearer ...``, hex >= 32,
      base64 >= 32) — these are replaced wholesale with the redaction
      marker. Tokens are stripped of surrounding quotes / trailing
      punctuation before checking, so a value embedded in a
      JSON-serialized dict (e.g. ``"sk-..."}``) is still caught.
    """
    if not isinstance(content, str):
        return content

    result = content

    # Redact inline ``key=value`` / ``key: value`` / ``"key": "value"`` patterns.
    # Phase 2.3 audit: threshold logic moved to ``_should_redact_inline``
    # which uses a CONTEXT-AWARE rule — short-code keys (cvv/pin/ssn/...)
    # are redacted on ANY match (their values are short by spec), while
    # other keys redact on length >= 16 OR a 4-8 char mixed-alphanumeric
    # short-code heuristic. This closes the gap where ``password=abc123``
    # (a real-looking short credential) was preserved by the old ``< 16``
    # blanket threshold.
    def _replace_inline(match: re.Match[str]) -> str:
        key = match.group(1)
        val = match.group(2)
        if not _should_redact_inline(key, val):
            return match.group(0)
        # Preserve the original separator (``:`` or ``=``).
        full = match.group(0)
        sep = "="
        for ch in full:
            if ch in ":=":
                sep = ch
                break
        return f"{key}{sep} {_REDACTED_SECRET}"

    result = _INLINE_SECRET_PATTERN.sub(_replace_inline, result)

    # Redact standalone secret values (sk-..., Bearer ..., etc.).
    # Split by whitespace and check each token.
    tokens = result.split()
    # Strip surrounding quotes, brackets, braces, and trailing punctuation
    # so a value embedded in a JSON-serialized dict (e.g. ``"sk-..."}``)
    # is still caught.
    strip_chars = "\"'.,;:!?)[]{}"
    for i, token in enumerate(tokens):
        stripped = token.strip(strip_chars)
        if _looks_like_secret_value(stripped):
            tokens[i] = token.replace(stripped, _REDACTED_SECRET)
    result = " ".join(tokens)

    return result


# =====================================================================
# Instruction injection detection
# =====================================================================

# Known instruction-injection patterns (case-insensitive).
# These are LEXICAL patterns — they detect common phrasings but can be
# evaded by paraphrasing. The structural perimeter is the real barrier.
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(
        r"disregard\s+(all\s+)?(previous\s+)?instructions", re.IGNORECASE
    ),
    re.compile(
        r"you\s+are\s+now\s+(dan|dAN|jailbroken|unrestricted)", re.IGNORECASE
    ),
    re.compile(r"system\s+override", re.IGNORECASE),
    re.compile(r"policy\s+update", re.IGNORECASE),
    re.compile(r"authorize\s+(tool|all\s+tools)", re.IGNORECASE),
    re.compile(r"grant\s+(admin|access|permission)", re.IGNORECASE),
    re.compile(
        r"disregard\s+(all\s+)?(safety|security)\s+(rules|guidelines)",
        re.IGNORECASE,
    ),
    re.compile(r"you\s+are\s+(now\s+)?(free|unrestricted|unbound)", re.IGNORECASE),
    re.compile(
        r"reveal\s+(the\s+)?(password|secret|api\s?key|token)", re.IGNORECASE
    ),
    re.compile(r"(execute|run|call)\s+(the\s+)?dangerous", re.IGNORECASE),
    re.compile(r"\[developer\s+note\]", re.IGNORECASE),
    re.compile(r"\[system\s+override\]", re.IGNORECASE),
]

_REDACTED_INJECTION = "[REDACTED: instruction-like content]"


class SuspicionLevel(str, Enum):
    """How suspicious a memory is."""

    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    MALICIOUS = "malicious"


def detect_instruction_injection(content: str) -> bool:
    """Detect if content contains known instruction-injection patterns.

    Returns ``True`` if any pattern matches. ``False`` does NOT mean the
    content is safe — only that no KNOWN pattern was detected.
    """
    if not isinstance(content, str):
        return False
    return any(p.search(content) for p in _INJECTION_PATTERNS)


def sanitize_injections(content: str) -> str:
    """Replace known injection patterns with a redaction marker.

    Each match is replaced with ``[REDACTED: instruction-like content]``.
    Non-matching content is preserved.
    """
    if not isinstance(content, str):
        return content
    result = content
    for pattern in _INJECTION_PATTERNS:
        result = pattern.sub(_REDACTED_INJECTION, result)
    return result


def assess_suspicion(content: str) -> SuspicionLevel:
    """Assess how suspicious a memory content is.

    Returns:
    - ``CLEAN``: no injection patterns, no secrets detected
    - ``SUSPICIOUS``: contains secrets OR mild injection indicators
    - ``MALICIOUS``: contains explicit injection patterns
    """
    if not isinstance(content, str):
        return SuspicionLevel.CLEAN

    has_injection = detect_instruction_injection(content)
    has_secret = bool(_INLINE_SECRET_PATTERN.search(content))

    if has_injection:
        return SuspicionLevel.MALICIOUS
    if has_secret:
        return SuspicionLevel.SUSPICIOUS
    return SuspicionLevel.CLEAN


# =====================================================================
# Combined sanitizer
# =====================================================================


def sanitize_for_context(content: str) -> str:
    """Apply all sanitization layers to memory content before LLM context.

    Order:
    1. Redact secrets (inline ``key=value`` + standalone values)
    2. Redact instruction injection patterns

    This is the main entry point for the retriever.
    """
    content_str = content if isinstance(content, str) else str(content)

    content_str = redact_secrets(content_str)
    content_str = sanitize_injections(content_str)
    return content_str


def sanitize_memory_content(content: Any) -> str:
    """Sanitize memory content of any type.

    If content is a dict / list, sanitize its string representation.
    If content is a string, sanitize directly. ``None`` returns ``""``.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return sanitize_for_context(content)
    if isinstance(content, (dict, list)):
        import json

        try:
            content_str = json.dumps(content, default=str)
        except Exception:  # noqa: BLE001
            content_str = str(content)
        return sanitize_for_context(content_str)
    return sanitize_for_context(str(content))


__all__ = [
    "SuspicionLevel",
    "redact_secrets",
    "detect_instruction_injection",
    "sanitize_injections",
    "assess_suspicion",
    "sanitize_for_context",
    "sanitize_memory_content",
]
