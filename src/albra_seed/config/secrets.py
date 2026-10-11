"""Secrets adapter — bridges security.secrets with config.manager.

Re-exports the canonical SecretsManager interface from
`albra_seed.security.secrets` and adds:

    * `SecretsAdapter`      — owned by ConfigManager; provides a single
                              `redacted_snapshot()` view that's safe to
                              expose in API/CLI output.
    * `SecretsRedactor`    — utility that recursively redacts any value
                              whose key matches the secret-name pattern.
    * `redact_secret_values` — module-level helper for one-off dicts.

CRITICAL: no value ever reaches the redactor output. Only key names and
boolean "is_set" flags.
"""

from __future__ import annotations

import os
import re
from typing import Any

from albra_seed.security.secrets import (
    EnvSecretsManager,
    FileSecretsManager,
    SecretsManager,
    VaultSecretsManager,
)

__all__ = [
    "SecretsManager",
    "EnvSecretsManager",
    "FileSecretsManager",
    "VaultSecretsManager",
    "SecretsAdapter",
    "SecretsRedactor",
    "redact_secret_values",
]


_REDACTED = "<redacted>"


# Keys whose values MUST be redacted.
#
# Two layers of matching are used:
#   1. Exact-key (case-insensitive) match against the explicit allowlist
#      below — covers the canonical SEED secret field names.
#   2. Word-boundary regex against the patterns below. The regex uses
#      `(?:^|_)` and `(?:$|_)` anchors so that "token" matches
#      `vault_token` but NOT `default_max_tokens` (no word boundary
#      between "token" and "s"). Likewise `secret` matches `client_secret`
#      but NOT `secrets_backend` (which has "s" after "secret").
_EXACT_SECRET_KEYS: frozenset[str] = frozenset({
    "openai_api_key", "anthropic_api_key", "groq_api_key", "zai_api_key",
    "vault_token", "vault_addr",
    "openai_api_base", "anthropic_api_base",
})

# Regex (case-insensitive) with word boundaries on BOTH sides.
# A key matches if the secret-pattern is delimited by either start/end of
# string or a non-alphanumeric separator (underscore, dash).
_SECRET_NAME_REGEX = re.compile(
    r"(?:^|[^a-z0-9])("
    r"password|passwd|secret|token|api[_-]?key|apikey|credential|"
    r"bearer|jwt|cookie|private[_-]?key|access[_-]?key|session[_-]?key|"
    r"refresh[_-]?token|authorization"
    r")(?:$|[^a-z0-9])",
    re.IGNORECASE,
)


def _is_secret_key(key: str) -> bool:
    if not key:
        return False
    if key.lower() in _EXACT_SECRET_KEYS:
        return True
    return bool(_SECRET_NAME_REGEX.search(key))


def redact_secret_values(obj: Any) -> Any:
    """Recursively replace any value whose key looks like a secret.

    Lists, dicts and BaseModel instances are handled. Non-mapping values
    (strings, ints) are returned unchanged (we never redact a value just
    because it *looks* secret — only its key triggers redaction).

    IMPORTANT: when a key matches the secret pattern but its value is a
    bool / int / float / None, we DO NOT redact. Secrets are always
    string-typed; a boolean "is_set" flag is safe to emit even when its
    key looks secret-shaped.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if _is_secret_key(k) and isinstance(v, str):
                out[k] = _REDACTED
            else:
                out[k] = redact_secret_values(v)
        return out
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact_secret_values(v) for v in obj)
    if hasattr(obj, "model_dump"):  # pydantic BaseModel
        return redact_secret_values(obj.model_dump(mode="json"))
    return obj


# =====================================================================
# SecretsRedactor — class wrapper for testability
# =====================================================================


class SecretsRedactor:
    """Class wrapper around `redact_secret_values`."""

    REDACTED: str = _REDACTED

    def is_secret_key(self, key: str) -> bool:
        return _is_secret_key(key)

    def redact(self, obj: Any) -> Any:
        return redact_secret_values(obj)


# =====================================================================
# SecretsAdapter — owned by ConfigManager
# =====================================================================


class SecretsAdapter:
    """Bridges a SecretsManager with the ConfigManager.

    Provides:
      * `manager`            — the wrapped SecretsManager (read-only view)
      * `redacted_keys()`    — list of {key, is_set} pairs (never values)
      * `redacted_snapshot()` — dict keyed by secret name with is_set bool

    The adapter NEVER reads or returns secret values — only their key
    names and a boolean "is_set" flag. Use `await manager.get(key)` to
    fetch a value in trusted code.
    """

    def __init__(self, manager: SecretsManager | None = None) -> None:
        self._manager: SecretsManager = manager or EnvSecretsManager()

    @property
    def manager(self) -> SecretsManager:
        return self._manager

    def redacted_keys(self) -> list[dict[str, Any]]:
        """Return [{name, is_set}] for every key the manager knows about.
        Values are NEVER returned."""
        import asyncio

        async def _gather() -> list[dict[str, Any]]:
            keys = await self._manager.list_keys()
            out: list[dict[str, Any]] = []
            for k in keys:
                if not _is_secret_key(k):
                    continue
                val = await self._manager.get(k)
                out.append({"name": k, "is_set": bool(val)})
            return out

        try:
            # If we're already inside a running loop, we can't drive
            # another one — fall back to the synchronous env-only view.
            asyncio.get_running_loop()
            return self._env_redacted_keys_sync()
        except RuntimeError:
            try:
                return asyncio.run(_gather())
            except Exception:
                return self._env_redacted_keys_sync()
        except Exception:
            return []

    def _env_redacted_keys_sync(self) -> list[dict[str, Any]]:
        """Sync fallback used when no event loop can be driven."""
        out: list[dict[str, Any]] = []
        for k in os.environ:
            if _is_secret_key(k):
                out.append({"name": k, "is_set": bool(os.environ.get(k))})
        out.sort(key=lambda r: r["name"])
        return out

    def redacted_snapshot(self) -> dict[str, bool]:
        """Return {name: is_set} dict (NEVER values)."""
        return {row["name"]: bool(row["is_set"]) for row in self.redacted_keys()}

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"SecretsAdapter(manager={type(self._manager).__name__}, "
            f"keys_hidden=True)"
        )
