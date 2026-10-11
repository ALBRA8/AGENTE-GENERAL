"""Config subpackage — centralized configuration management.

Layers (in priority order, last wins):
    DEFAULT CONFIG   — built-in defaults baked into SeedConfig / AgentConfig
    ENV CONFIG       — process environment variables (ALBRA_* prefix)
    FILE CONFIG      — .env / YAML / JSON file
    RUNTIME CONFIG   — overrides applied at agent init time

Public API:
    ConfigManager     — holds SeedConfig + AgentConfig; load/save/get/set/reload
    ConfigLoader      — loads from .env / YAML / JSON / pure env vars
    ConfigValidator   — validates config values (ranges, types, required)
    SecretsAdapter    — bridges SecretsManager <-> ConfigManager

CRITICAL RULES:
    * Secrets are NEVER serialized to disk by ConfigManager.
    * Any output (dict, JSON, YAML) MUST run through redaction.
    * CORE_ONLY mode is preserved: no extra deps required to use this
      subpackage (YAML support is lazy via PyYAML).
"""

from albra_seed.config.loader import ConfigLoader, LoadResult
from albra_seed.config.manager import ConfigManager
from albra_seed.config.secrets import (
    SecretsAdapter,
    SecretsRedactor,
    redact_secret_values,
)
from albra_seed.config.validator import (
    ConfigValidationError,
    ConfigValidator,
    ValidationResult,
)

__all__ = [
    "ConfigManager",
    "ConfigLoader",
    "LoadResult",
    "ConfigValidator",
    "ConfigValidationError",
    "ValidationResult",
    "SecretsAdapter",
    "SecretsRedactor",
    "redact_secret_values",
]
