"""ConfigManager — holds SeedConfig + AgentConfig.

Public API:
    manager = ConfigManager()
    manager.load()                     # defaults → env → optional file
    manager.save(path)                 # write redacted JSON to disk
    manager.get("api_port")            # dot-path accessor
    manager.set("api_port", 9000)      # runtime override (in-memory)
    manager.reload()                   # re-load from source of truth
    manager.redacted_snapshot()        # dict safe for API/CLI output
    manager.validate()                 # returns ValidationResult

CRITICAL:
    * Secrets are NEVER persisted to disk via `save()`.
    * `redacted_snapshot()` is the only method that returns config as a
      dict — and it strips secret values.
    * `set()` only mutates in-memory state; the original file (if any)
      is left untouched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from albra_seed.config.loader import ConfigLoader, LoadResult
from albra_seed.config.secrets import (
    SecretsAdapter,
    redact_secret_values,
)
from albra_seed.config.validator import (
    ConfigValidationError,
    ConfigValidator,
    ValidationResult,
)
from albra_seed.core.config import AgentConfig, SeedConfig


class ConfigManager:
    """Centralized runtime config holder."""

    def __init__(
        self,
        *,
        seed: SeedConfig | None = None,
        agent: AgentConfig | None = None,
        loader: ConfigLoader | None = None,
        secrets: SecretsAdapter | None = None,
        validator: ConfigValidator | None = None,
    ) -> None:
        self._seed: SeedConfig = seed or SeedConfig()
        self._agent: AgentConfig = agent or AgentConfig()
        self._loader: ConfigLoader = loader or ConfigLoader()
        self._secrets: SecretsAdapter = secrets or SecretsAdapter()
        self._validator: ConfigValidator = validator or ConfigValidator()
        self._source: str = "defaults"
        self._source_path: str | None = None
        self._overrides: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Properties                                                          #
    # ------------------------------------------------------------------ #

    @property
    def seed(self) -> SeedConfig:
        return self._seed

    @property
    def agent(self) -> AgentConfig:
        return self._agent

    @property
    def secrets(self) -> SecretsAdapter:
        return self._secrets

    @property
    def source(self) -> str:
        return self._source

    @property
    def source_path(self) -> str | None:
        return self._source_path

    # ------------------------------------------------------------------ #
    # Load / reload                                                       #
    # ------------------------------------------------------------------ #

    def load(
        self,
        path: str | Path | None = None,
        *,
        prefer: str | None = None,
    ) -> LoadResult:
        """Load SeedConfig from defaults/env/file and adopt it."""
        result = self._loader.load(path, prefer=prefer)
        self._seed = result.config
        self._source = result.source
        self._source_path = result.path
        self._overrides.clear()
        return result

    def reload(self) -> LoadResult:
        """Re-load from the last source/path."""
        return self.load(self._source_path)

    # ------------------------------------------------------------------ #
    # Get / set                                                           #
    # ------------------------------------------------------------------ #

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by dot-path.

        Looks up `seed` first, then `agent`. Override values (set via
        `set()`) take precedence.
        """
        if key in self._overrides:
            return self._overrides[key]
        for root in (self._seed, self._agent):
            if hasattr(root, key):
                return getattr(root, key)
        # Dot-path into seed only (e.g. "default_provider").
        if "." in key:
            head, _, tail = key.partition(".")
            obj = getattr(self._seed, head, None) or getattr(self._agent, head, None)
            cur: Any = obj
            for part in tail.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    cur = getattr(cur, part, None)
                if cur is None:
                    return default
            return cur
        return default

    def set(self, key: str, value: Any, *, persist: bool = False) -> None:
        """Set an in-memory override.

        `persist=True` additionally mutates the underlying SeedConfig /
        AgentConfig instance via setattr. Override values are NEVER
        written to disk by this method.
        """
        self._overrides[key] = value
        if persist:
            if hasattr(self._seed, key):
                setattr(self._seed, key, value)
            elif hasattr(self._agent, key):
                setattr(self._agent, key, value)

    # ------------------------------------------------------------------ #
    # Save                                                                #
    # ------------------------------------------------------------------ #

    def save(self, path: str | Path, *, include_secrets: bool = False) -> Path:
        """Write SeedConfig to a JSON file.

        Secrets are STRIPPED unless `include_secrets=True` is explicitly
        passed. Even then, secrets are written to a 0600-perm file (best
        effort on non-POSIX platforms).
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = self._seed.model_dump(mode="json")
        if not include_secrets:
            data = redact_secret_values(data)
        p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass  # Non-POSIX (e.g. Windows) — best effort
        return p

    # ------------------------------------------------------------------ #
    # Validation                                                          #
    # ------------------------------------------------------------------ #

    def validate(self) -> ValidationResult:
        return self._validator.validate(self._seed, self._agent)

    def validate_or_raise(self) -> None:
        self._validator.validate_or_raise(self._seed, self._agent)

    # ------------------------------------------------------------------ #
    # Redacted snapshot                                                   #
    # ------------------------------------------------------------------ #

    def redacted_snapshot(self) -> dict[str, Any]:
        """Return a single dict with SeedConfig + AgentConfig + secrets
        info, with every secret value replaced by '<redacted>'."""
        seed_dict = redact_secret_values(self._seed.model_dump(mode="json"))
        agent_dict = redact_secret_values(self._agent.model_dump(mode="json"))
        return {
            "source": self._source,
            "source_path": self._source_path,
            "overrides": list(self._overrides.keys()),
            "seed": seed_dict,
            "agent": agent_dict,
            "secrets": self._secrets.redacted_snapshot(),
        }

    def to_redacted_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.redacted_snapshot(), indent=indent, sort_keys=True, default=str)


__all__ = [
    "ConfigManager",
    "ConfigLoader",
    "ConfigValidator",
    "ConfigValidationError",
    "ValidationResult",
    "SecretsAdapter",
]
