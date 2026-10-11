"""ErrorClassifier — maps exceptions to ErrorClass.

ErrorClass determines which RecoveryStrategy is appropriate:
  * TRANSIENT   → retry (network blip, rate-limit)
  * PERSISTENT  → fallback or restart component (logic error, bad input)
  * SECURITY    → escalate, NEVER auto-recover (permission denied, auth fail)
  * RESOURCE    → degrade or restart (OOM, disk full, queue full)
  * EXTERNAL    → retry with backoff (3rd-party API down)
  * UNKNOWN     → escalate, NEVER auto-recover

The classifier is conservative: when in doubt, classify as UNKNOWN so
the RecoveryManager refuses to auto-act.
"""

from __future__ import annotations

from enum import Enum


class ErrorClass(str, Enum):
    TRANSIENT = "transient"
    PERSISTENT = "persistent"
    SECURITY = "security"
    RESOURCE = "resource"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


# String-substring matching against exception type name + message.
# Substring matching keeps the classifier resilient to wrapper classes
# (e.g. `openai.APIConnectionError`, `anthropic.RateLimitError`).
_TRANSIENT_PATTERNS = (
    "timeout", "timed out", "connection", "temporar", "retry",
    "rate_limit", "ratelimit", "too many requests", "503", "504",
    "connectionreset", "connectionaborted", "connectionrefused",
)

_PERSISTENT_PATTERNS = (
    "valueerror", "typeerror", "keyerror", "attributeerror",
    "notimplementederror", "invalid", "bad request", "400", "422",
    "schema", "validation",
)

_SECURITY_PATTERNS = (
    "permissiondenied", "permissiondeniederror", "permissionerror",
    "permission denied", "permissionerror", "unauthorized",
    "forbidden", "401", "403", "authentication", "authorization",
    "access denied", "not allowed", "denied",
)

_RESOURCE_PATTERNS = (
    "outofmemory", "out of memory", "oom", "disk full", "no space left",
    "queue full", "capacity", "quota exceeded", "resourceexhausted",
)

_EXTERNAL_PATTERNS = (
    "apierror", "apiconnectionerror", "serviceunavailable",
    "external", "upstream", "provider", "502",
)


def _match_any(text: str, patterns: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(p in t for p in patterns)


class ErrorClassifier:
    """Classifies exceptions into ErrorClass values.

    Determined by:
      1. Substring matching on type-name + message (priority order).
      2. Default: UNKNOWN.
    """

    def classify(self, exc: BaseException) -> ErrorClass:
        type_name = type(exc).__name__
        msg = str(exc)
        text = f"{type_name} {msg}"

        # SECURITY wins — never silently retry a security error.
        if _match_any(text, _SECURITY_PATTERNS):
            return ErrorClass.SECURITY
        if _match_any(text, _RESOURCE_PATTERNS):
            return ErrorClass.RESOURCE
        if _match_any(text, _TRANSIENT_PATTERNS):
            return ErrorClass.TRANSIENT
        if _match_any(text, _EXTERNAL_PATTERNS):
            return ErrorClass.EXTERNAL
        if _match_any(text, _PERSISTENT_PATTERNS):
            return ErrorClass.PERSISTENT
        return ErrorClass.UNKNOWN

    def classify_with_context(
        self,
        exc: BaseException,
        *,
        component: str | None = None,
        action: str | None = None,
    ) -> ErrorClass:
        """Classify with additional context (component / action).

        The classifier is monotonic — context can only NARROW the
        classification toward SECURITY (escalate) or RESOURCE (degrade),
        never widen it to TRANSIENT (auto-retry).
        """
        base = self.classify(exc)
        text = f"{component or ''} {action or ''} {exc!s}".lower()
        if _match_any(text, _SECURITY_PATTERNS):
            return ErrorClass.SECURITY
        if _match_any(text, _RESOURCE_PATTERNS):
            return ErrorClass.RESOURCE
        return base


__all__ = ["ErrorClass", "ErrorClassifier"]
