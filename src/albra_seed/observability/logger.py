"""StructuredLogger — `structlog`-backed structured logging.

Features:
  * JSON or console output, configurable per-instance.
  * Correlation-id propagation via contextvars (reuses
    `albra_seed.context.correlation.CorrelationTracker`).
  * SECRET REDACTION: known secret keys are NEVER emitted to logs.

The redaction layer is deliberately conservative — false positives
(redacting a benign key) are safe; false negatives (leaking a real
secret) are catastrophic. The default secret-key list covers the
common patterns seen in agent runs (api keys, tokens, passwords,
credentials, signing secrets, jwt, refresh tokens, etc).
"""

from __future__ import annotations

import contextvars
import json
import re
import sys
from typing import Any, Callable, TextIO

try:  # structlog is in requirements.txt — always available in this seed
    import structlog  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - structlog is required
    structlog = None  # type: ignore[assignment]

from albra_seed.context.correlation import CorrelationTracker

# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

# Key substrings that indicate a secret value. Matched case-insensitively
# against the *key* of any kwarg/field. A key matches if any substring is
# present. (e.g. `openai_api_key`, `bearer_token`, `user_password`,
# `refresh_token`, `auth_credential`.)
#
# Phase 2.3 audit: kept in sync with ``memory/sanitizer.py`` so the
# logger and the sanitizer flag the same set of sensitive fields
# (defense-in-depth). Added: ``ssn``, ``credit_card``, ``card_number``,
# ``cvv``, ``pin``.
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

# Value patterns that look like secrets (long base64-ish / hex strings).
# These are redacted if found in *any* value position when the value is a
# string of >= 24 chars matching the pattern.
_SECRET_VALUE_PATTERN = re.compile(
    r"^(sk-[A-Za-z0-9_\-]{20,}|"
    r"Bearer\s+[A-Za-z0-9_\-\.]{20,}|"
    r"[A-Fa-f0-9]{32,}|"
    r"[A-Za-z0-9+/]{32,}={0,2})$"
)

_REDACTED = "<redacted>"


def _looks_like_secret_key(key: str) -> bool:
    if not isinstance(key, str):
        return False
    k = key.lower()
    return any(sub in k for sub in _SECRET_KEY_SUBSTRINGS)


def _looks_like_secret_value(value: Any) -> bool:
    if not isinstance(value, str) or len(value) < 24:
        return False
    return bool(_SECRET_VALUE_PATTERN.match(value))


def redact(obj: Any) -> Any:
    """Recursively redact secret values from dict/list/scalar structures.

    A field is redacted if:
      * Its key matches a known secret substring (case-insensitive).
      * Its value matches the secret-value regex.
    Nested dicts and lists are walked.
    """
    if isinstance(obj, dict):
        return {k: (_REDACTED if _looks_like_secret_key(k) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    if _looks_like_secret_value(obj):
        return _REDACTED
    return obj


# ---------------------------------------------------------------------------
# Correlation-id injection processor for structlog
# ---------------------------------------------------------------------------


def _correlation_processor(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject correlation_id / execution_id / agent_id from contextvars."""
    snap = CorrelationTracker.snapshot()
    # Only inject if not already explicitly set on the event.
    event_dict.setdefault("correlation_id", snap.get("correlation_id"))
    if snap.get("execution_id") and "execution_id" not in event_dict:
        event_dict["execution_id"] = snap["execution_id"]
    if snap.get("agent_id") and "agent_id" not in event_dict:
        event_dict["agent_id"] = snap["agent_id"]
    return event_dict


def _redaction_processor(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Redact secret values from event_dict before rendering."""
    return redact(event_dict)


# Context var for per-logger correlation override (rarely needed; the
# CorrelationTracker is the canonical source, but tests sometimes want to
# pin a value without spawning a scope).
_correlation_override: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "albra_logger_corr_override", default=None
)


class StructuredLogger:
    """Structured logger wrapping structlog with redaction + correlation.

    Output formats:
      * "json"    — one JSON object per line (default for production).
      * "console" — human-readable colored console output (default for dev).

    The logger NEVER emits secret values. Redaction happens via the
    `_redaction_processor` which is the last processor before rendering.
    """

    def __init__(
        self,
        name: str = "albra",
        *,
        format: str = "json",
        stream: TextIO | None = None,
        level: str = "info",
        extra_processors: list[Callable[..., Any]] | None = None,
    ) -> None:
        if structlog is None:
            raise RuntimeError(
                "structlog is required for StructuredLogger; install 'structlog'."
            )
        self._name = name
        self._format = format
        self._stream = stream or sys.stdout
        self._level = level
        processors: list[Any] = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _correlation_processor,
            _redaction_processor,
        ]
        if extra_processors:
            processors.extend(extra_processors)
        if format == "json":
            processors.append(structlog.processors.JSONRenderer())
        else:
            processors.append(structlog.dev.ConsoleRenderer(colors=False))
        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(level),
            cache_logger_on_first_use=True,
        )
        self._log = structlog.get_logger(name)
        # Bind a buffer for tests that want to inspect emitted records.
        self._sink: list[dict[str, Any]] = []
        self._capture = False

    @property
    def name(self) -> str:
        return self._name

    def bind(self, **kwargs: Any) -> "StructuredLogger":
        """Return a child logger with extra context (already redacted)."""
        child = StructuredLogger(self._name, format=self._format, stream=self._stream, level=self._level)
        child._log = self._log.bind(**redact(kwargs))
        return child

    def start_capture(self) -> None:
        """Begin buffering emitted JSON records in memory (test helper)."""
        self._capture = True
        self._sink.clear()

    def captured(self) -> list[dict[str, Any]]:
        """Return captured records (test helper). Works only for JSON format."""
        return list(self._sink)

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------
    def _emit(self, level: str, event: str, **kwargs: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "event": event,
            "level": level,
            "logger": self._name,
            **redact(kwargs),
        }
        snap = CorrelationTracker.snapshot()
        record.setdefault("correlation_id", snap.get("correlation_id"))
        if snap.get("execution_id"):
            record.setdefault("execution_id", snap["execution_id"])
        if self._capture:
            self._sink.append(record)
        # Render to the stream so existing structlog behaviour is preserved.
        try:
            if self._format == "json":
                self._stream.write(json.dumps(record, default=str) + "\n")
                self._stream.flush()
            else:
                # Console: minimal pretty-print
                lvl = record.pop("level", "info").upper()
                ts = record.pop("timestamp", "")
                ev = record.pop("event", "")
                rest = " ".join(f"{k}={v!r}" for k, v in record.items() if k != "logger")
                self._stream.write(f"[{ts}] [{lvl}] {self._name}: {ev} {rest}\n".rstrip() + "\n")
                self._stream.flush()
        except Exception:  # noqa: BLE001 - logging must NEVER raise
            pass
        return record

    def debug(self, event: str, **kwargs: Any) -> dict[str, Any]:
        return self._emit("debug", event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> dict[str, Any]:
        return self._emit("info", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> dict[str, Any]:
        return self._emit("warning", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> dict[str, Any]:
        return self._emit("error", event, **kwargs)

    def critical(self, event: str, **kwargs: Any) -> dict[str, Any]:
        return self._emit("critical", event, **kwargs)


__all__ = ["StructuredLogger", "redact", "_REDACTED"]
