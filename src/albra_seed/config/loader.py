"""ConfigLoader — load config from .env / YAML / JSON / pure env vars.

Loader precedence (first found wins for a given key):
    1. Explicit runtime overrides (passed as kwargs)
    2. Pure env vars (ALBRA_* + OPENAI_API_KEY etc.)
    3. .env file (python-dotenv; stdlib fallback if dotenv missing)
    4. YAML file (requires PyYAML; lazy)
    5. JSON file (always supported)
    6. Built-in defaults from SeedConfig.from_env()

CRITICAL: secrets read from a file are kept ONLY in memory — the loader
never writes them anywhere and never logs their values.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from albra_seed.core.config import SeedConfig


# =====================================================================
# LoadResult — what the loader produced
# =====================================================================


@dataclass
class LoadResult:
    """Outcome of a config load operation.

    `source` is one of: "defaults", "env", "dotenv", "yaml", "json".
    `path` is the resolved file path (if any).
    `overrides_applied` is a list of keys that were overridden.
    """

    config: SeedConfig
    source: str = "defaults"
    path: str | None = None
    overrides_applied: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "path": self.path,
            "overrides_applied": list(self.overrides_applied),
            "notes": list(self.notes),
        }


# =====================================================================
# ConfigLoader
# =====================================================================


class ConfigLoader:
    """Loads SeedConfig from defaults / env / file.

    The loader is intentionally side-effect free apart from
    `load_dotenv()` (which mutates `os.environ`). It does NOT mutate any
    global registry.
    """

    def __init__(self, env_prefix: str = "ALBRA_") -> None:
        self._prefix = env_prefix

    @property
    def prefix(self) -> str:
        return self._prefix

    # ------------------------------------------------------------------ #
    # Pure env                                                            #
    # ------------------------------------------------------------------ #

    def load_from_env(self) -> LoadResult:
        """Load SeedConfig purely from environment variables."""
        config = SeedConfig.from_env()
        return LoadResult(
            config=config,
            source="env",
            overrides_applied=self._env_keys_present(),
            notes=["loaded from process environment"],
        )

    # ------------------------------------------------------------------ #
    # .env file                                                           #
    # ------------------------------------------------------------------ #

    def load_from_dotenv(self, path: str | Path) -> LoadResult:
        """Load SeedConfig from a .env file.

        Uses python-dotenv if available; otherwise falls back to a tiny
        stdlib-only parser (KEY=VALUE lines, optional `export ` prefix,
        `#` comments, no interpolation).
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f".env file not found: {p}")
        self._apply_dotenv(p)
        config = SeedConfig.from_env(env_path=None)
        return LoadResult(
            config=config,
            source="dotenv",
            path=str(p),
            overrides_applied=self._env_keys_present(),
            notes=[f"applied .env file: {p}"],
        )

    # ------------------------------------------------------------------ #
    # JSON                                                                #
    # ------------------------------------------------------------------ #

    def load_from_json(self, path: str | Path) -> LoadResult:
        """Load SeedConfig from a JSON file. Keys map directly to
        SeedConfig fields (e.g. `default_provider`, `api_port`)."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"config JSON not found: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"config JSON root must be an object: {p}")
        config = self._build_config_from_dict(data)
        return LoadResult(
            config=config,
            source="json",
            path=str(p),
            overrides_applied=list(data.keys()),
            notes=[f"loaded JSON config: {p}"],
        )

    # ------------------------------------------------------------------ #
    # YAML (lazy PyYAML)                                                  #
    # ------------------------------------------------------------------ #

    def load_from_yaml(self, path: str | Path) -> LoadResult:
        """Load SeedConfig from a YAML file. PyYAML is lazy-imported."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"config YAML not found: {p}")
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - optional dep
            raise ImportError(
                "PyYAML is required to read YAML config; install 'pyyaml' "
                "or use a .json file."
            ) from exc
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"config YAML root must be a mapping: {p}")
        config = self._build_config_from_dict(data)
        return LoadResult(
            config=config,
            source="yaml",
            path=str(p),
            overrides_applied=list(data.keys()),
            notes=[f"loaded YAML config: {p}"],
        )

    # ------------------------------------------------------------------ #
    # Auto-detect                                                         #
    # ------------------------------------------------------------------ #

    def load(
        self,
        path: str | Path | None = None,
        *,
        prefer: str | None = None,
    ) -> LoadResult:
        """Auto-detect load strategy. If `path` is None, load from env.

        `prefer` overrides file-suffix detection; one of:
            "env" | "dotenv" | "yaml" | "json".
        """
        if path is None:
            return self.load_from_env()
        p = Path(path)
        suffix = (prefer or p.suffix.lstrip(".")).lower()
        if suffix in {"env", "dotenv"}:
            return self.load_from_dotenv(p)
        if suffix in {"yaml", "yml"}:
            return self.load_from_yaml(p)
        if suffix == "json":
            return self.load_from_json(p)
        raise ValueError(f"cannot auto-detect loader for: {p} (prefer={prefer})")

    # ------------------------------------------------------------------ #
    # Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _build_config_from_dict(self, data: dict[str, Any]) -> SeedConfig:
        """Construct SeedConfig from a dict. Env vars still apply on top."""
        # First apply env, then layer dict on top via model_copy.
        env_cfg = SeedConfig.from_env()
        merged = env_cfg.model_dump()
        merged.update({k: v for k, v in data.items() if k in merged})
        return SeedConfig.model_validate(merged)

    def _env_keys_present(self) -> list[str]:
        return sorted(
            k for k in os.environ if k.startswith(self._prefix)
        ) + sorted(
            k for k in (
                "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
                "ZAI_API_KEY", "OLLAMA_BASE_URL",
            ) if k in os.environ
        )

    @staticmethod
    def _apply_dotenv(path: Path) -> None:
        """Populate os.environ from a .env file. Uses python-dotenv if
        available; otherwise falls back to a minimal stdlib parser."""
        try:
            from dotenv import load_dotenv  # type: ignore[import-untyped]
            load_dotenv(path, override=False)
            return
        except ImportError:
            pass
        # Stdlib fallback parser.
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


__all__ = ["ConfigLoader", "LoadResult"]
