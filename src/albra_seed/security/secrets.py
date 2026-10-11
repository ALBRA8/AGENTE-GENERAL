"""Secrets management — NEVER log secret values.

Three backends:
  * EnvSecretsManager   — reads from os.environ (default, CORE_ONLY).
  * FileSecretsManager  — reads from a JSON or YAML file (no PyYAML dep:
                          YAML support is lazy and degrades to JSON-only).
  * VaultSecretsManager — lazy-imports `hvac` (HashiCorp Vault). Raises
                          ImportError at construction if `hvac` is missing.

Every manager exposes the same async interface:
    get(key) -> str | None
    set(key, value) -> None            # may be no-op for read-only backends
    delete(key) -> bool
    list_keys() -> list[str]

RULES (enforced by `_safe_log`):
  - Secrets are NEVER logged. Only the key name is logged.
  - The `__repr__` of secrets managers NEVER includes values.
  - Return values of `get` MUST be handled by the caller, never logged.

The seed ships no real secrets. Only interfaces + managers.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger("albra.security.secrets")

_REDACTED = "<redacted>"


def _safe_log(event: str, **kwargs: Any) -> None:
    """Log a secrets event WITHOUT ever including secret values.

    Strip any kwarg whose name suggests a secret value ('value',
    'secret', 'token', 'password', 'credential') and replace with
    '<redacted>'. Key names are safe to log.
    """
    safe = {}
    for k, v in kwargs.items():
        if k.lower() in {"value", "secret", "token", "password", "credential"}:
            safe[k] = _REDACTED
        else:
            safe[k] = v
    log.info(event, **safe)


class SecretsManager(ABC):
    """Universal secrets manager interface.

    Implementations MUST:
      * NEVER log returned values.
      * NEVER include values in __repr__ or exception messages.
      * Treat missing keys as `None` (not an error).
    """

    name: str = "base"
    is_read_only: bool = False

    @abstractmethod
    async def get(self, key: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    async def set(self, key: str, value: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def list_keys(self) -> list[str]:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"{type(self).__name__}(name={self.name!r}, read_only={self.is_read_only})"


class EnvSecretsManager(SecretsManager):
    """Reads secrets from process environment variables.

    Read-only: `set` updates the in-process `os.environ` (does NOT persist
    to disk). `delete` removes from `os.environ`.
    """

    name = "env"
    is_read_only = False

    def __init__(self, prefix: str = "") -> None:
        self._prefix = prefix

    def _env_key(self, key: str) -> str:
        return f"{self._prefix}{key}" if self._prefix else key

    async def get(self, key: str) -> str | None:
        env_key = self._env_key(key)
        value = os.environ.get(env_key)
        _safe_log("secrets.get", backend="env", key=key, found=value is not None)
        return value

    async def set(self, key: str, value: str) -> None:
        os.environ[self._env_key(key)] = value
        _safe_log("secrets.set", backend="env", key=key)

    async def delete(self, key: str) -> bool:
        env_key = self._env_key(key)
        if env_key in os.environ:
            del os.environ[env_key]
            _safe_log("secrets.delete", backend="env", key=key, removed=True)
            return True
        _safe_log("secrets.delete", backend="env", key=key, removed=False)
        return False

    async def list_keys(self) -> list[str]:
        if not self._prefix:
            return sorted(os.environ.keys())
        return sorted(
            k[len(self._prefix):]
            for k in os.environ
            if k.startswith(self._prefix)
        )


class FileSecretsManager(SecretsManager):
    """Reads secrets from a JSON or YAML file.

    YAML support is lazy: if PyYAML is not installed, .yaml/.yml files
    raise ImportError when constructed (caller must handle gracefully).
    JSON is always supported (stdlib).
    """

    name = "file"
    is_read_only = False

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        text = self._path.read_text(encoding="utf-8")
        suffix = self._path.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover - optional dep
                raise ImportError(
                    "PyYAML is required to read YAML secrets files; "
                    "install 'pyyaml' or use a .json file."
                ) from exc
            data = yaml.safe_load(text) or {}
            if not isinstance(data, dict):
                raise ValueError(f"secrets file {self._path} is not a mapping")
            self._data = {str(k): str(v) for k, v in data.items()}
        else:
            data = json.loads(text) if text.strip() else {}
            if not isinstance(data, dict):
                raise ValueError(f"secrets file {self._path} is not a JSON object")
            self._data = {str(k): str(v) for k, v in data.items()}

    def _save(self) -> None:
        suffix = self._path.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover - optional dep
                raise ImportError(
                    "PyYAML is required to write YAML secrets files."
                ) from exc
            self._path.write_text(
                yaml.safe_dump({k: str(v) for k, v in self._data.items()}, sort_keys=True),
                encoding="utf-8",
            )
        else:
            self._path.write_text(
                json.dumps(self._data, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    async def get(self, key: str) -> str | None:
        value = self._data.get(key)
        _safe_log("secrets.get", backend="file", key=key, found=value is not None)
        return value

    async def set(self, key: str, value: str) -> None:
        self._data[key] = value
        self._save()
        _safe_log("secrets.set", backend="file", key=key)

    async def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            self._save()
            _safe_log("secrets.delete", backend="file", key=key, removed=True)
            return True
        return False

    async def list_keys(self) -> list[str]:
        return sorted(self._data.keys())


class VaultSecretsManager(SecretsManager):
    """HashiCorp Vault secrets manager (lazy `hvac` import).

    Constructed with a Vault URL + token (taken from env or passed in).
    Raises ImportError at construction if `hvac` is not installed.
    The token is NEVER logged — only the URL and mount path are.
    """

    name = "vault"
    is_read_only = False

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        mount_path: str = "secret",
    ) -> None:
        try:
            import hvac  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "hvac is required for VaultSecretsManager; install 'hvac' or "
                "use EnvSecretsManager / FileSecretsManager."
            ) from exc
        self._hvac = hvac
        self._url = url or os.environ.get("VAULT_ADDR", "")
        self._token = token or os.environ.get("VAULT_TOKEN", "")
        self._mount_path = mount_path
        if not self._url:
            raise ValueError("VAULT_ADDR not set and no url provided")
        self._client = hvac.Client(url=self._url, token=self._token)
        _safe_log(
            "secrets.vault.init",
            url=self._url,
            mount_path=self._mount_path,
            has_token=bool(self._token),
        )

    async def get(self, key: str) -> str | None:
        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                path=key, mount_point=self._mount_path
            )
        except Exception:  # noqa: BLE001
            _safe_log("secrets.get", backend="vault", key=key, found=False, error=True)
            return None
        data = (resp or {}).get("data", {}).get("data", {})
        # KV v2 stores a dict; return first string-typed value if no `value` key.
        if "value" in data:
            _safe_log("secrets.get", backend="vault", key=key, found=True)
            return str(data["value"])
        for v in data.values():
            if isinstance(v, str):
                _safe_log("secrets.get", backend="vault", key=key, found=True)
                return v
        _safe_log("secrets.get", backend="vault", key=key, found=False)
        return None

    async def set(self, key: str, value: str) -> None:
        self._client.secrets.kv.v2.create_or_update_secret(
            path=key, mount_point=self._mount_path, secret={"value": value}
        )
        _safe_log("secrets.set", backend="vault", key=key)

    async def delete(self, key: str) -> bool:
        try:
            self._client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=key, mount_point=self._mount_path
            )
            _safe_log("secrets.delete", backend="vault", key=key, removed=True)
            return True
        except Exception:  # noqa: BLE001
            _safe_log("secrets.delete", backend="vault", key=key, removed=False, error=True)
            return False

    async def list_keys(self) -> list[str]:
        try:
            resp = self._client.secrets.kv.v2.list_secrets(
                path="", mount_point=self._mount_path
            )
            return list((resp or {}).get("data", {}).get("keys", []))
        except Exception:  # noqa: BLE001
            return []


__all__ = [
    "SecretsManager",
    "EnvSecretsManager",
    "FileSecretsManager",
    "VaultSecretsManager",
]
