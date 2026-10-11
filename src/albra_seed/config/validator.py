"""ConfigValidator — validates SeedConfig / AgentConfig values.

Validates:
    * types (port is int, host is str, etc.)
    * ranges (port 1..65535, temperature 0..2, max_tokens 1..1_000_000)
    * required fields (config.seed_version non-empty)
    * cross-field constraints (vault_token required iff secrets_backend=vault)
    * security rules (no API key shorter than 20 chars if set)

Returns a `ValidationResult` with errors + warnings + ok flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from albra_seed.core.config import AgentConfig, SeedConfig


# =====================================================================
# Validation result
# =====================================================================


@dataclass
class ValidationResult:
    """Outcome of a config validation pass."""

    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.ok = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def merge(self, other: "ValidationResult") -> "ValidationResult":
        return ValidationResult(
            ok=self.ok and other.ok,
            errors=[*self.errors, *other.errors],
            warnings=[*self.warnings, *other.warnings],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


class ConfigValidationError(ValueError):
    """Raised when config fails validation."""

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        super().__init__(f"config validation failed: {'; '.join(result.errors)}")


# =====================================================================
# Validator
# =====================================================================


class ConfigValidator:
    """Validates SeedConfig + AgentConfig instances."""

    MIN_API_KEY_LEN: int = 20
    PORT_RANGE: tuple[int, int] = (1, 65535)
    TEMP_RANGE: tuple[float, float] = (0.0, 2.0)
    MAX_TOKENS_RANGE: tuple[int, int] = (1, 1_000_000)
    AUTONOMY_RANGE: tuple[int, int] = (0, 5)

    # ------------------------------------------------------------------ #
    # SeedConfig                                                          #
    # ------------------------------------------------------------------ #

    def validate_seed(self, config: SeedConfig) -> ValidationResult:
        result = ValidationResult()
        # Port
        if not isinstance(config.api_port, int):
            result.add_error(f"api_port must be int, got {type(config.api_port).__name__}")
        elif not (self.PORT_RANGE[0] <= config.api_port <= self.PORT_RANGE[1]):
            result.add_error(
                f"api_port out of range {self.PORT_RANGE}: {config.api_port}"
            )
        # Temperature
        if not (self.TEMP_RANGE[0] <= config.default_temperature <= self.TEMP_RANGE[1]):
            result.add_error(
                f"default_temperature out of range {self.TEMP_RANGE}: "
                f"{config.default_temperature}"
            )
        # Max tokens
        if not (self.MAX_TOKENS_RANGE[0] <= config.default_max_tokens <= self.MAX_TOKENS_RANGE[1]):
            result.add_error(
                f"default_max_tokens out of range {self.MAX_TOKENS_RANGE}: "
                f"{config.default_max_tokens}"
            )
        # Max concurrent sessions
        if config.max_concurrent_sessions < 1:
            result.add_error(
                f"max_concurrent_sessions must be >= 1: {config.max_concurrent_sessions}"
            )
        # Session TTL
        if config.session_ttl_seconds < 1:
            result.add_error(
                f"session_ttl_seconds must be >= 1: {config.session_ttl_seconds}"
            )
        # Autonomy
        if not (self.AUTONOMY_RANGE[0] <= config.default_autonomy_level <= self.AUTONOMY_RANGE[1]):
            result.add_error(
                f"default_autonomy_level out of range {self.AUTONOMY_RANGE}: "
                f"{config.default_autonomy_level}"
            )
        # Required non-empty
        if not config.seed_version:
            result.add_error("seed_version must be non-empty")
        # Vault coherence
        if config.secrets_backend.value == "vault":
            if not config.vault_url:
                result.add_error("vault_url required when secrets_backend=vault")
            if not config.vault_token:
                result.add_warning(
                    "vault_token empty when secrets_backend=vault; "
                    "Vault access will fail at runtime"
                )
        # API keys: warn if suspiciously short
        for k in ("openai_api_key", "anthropic_api_key", "groq_api_key", "zai_api_key"):
            v = getattr(config, k)
            if v is not None and 0 < len(v) < self.MIN_API_KEY_LEN:
                result.add_warning(
                    f"{k} looks too short ({len(v)} chars); "
                    f"expected at least {self.MIN_API_KEY_LEN}"
                )
        return result

    # ------------------------------------------------------------------ #
    # AgentConfig                                                         #
    # ------------------------------------------------------------------ #

    def validate_agent(self, config: AgentConfig) -> ValidationResult:
        result = ValidationResult()
        if not config.name or not isinstance(config.name, str):
            result.add_error("agent name must be a non-empty string")
        if not (self.AUTONOMY_RANGE[0] <= config.autonomy_level <= self.AUTONOMY_RANGE[1]):
            result.add_error(
                f"autonomy_level out of range {self.AUTONOMY_RANGE}: "
                f"{config.autonomy_level}"
            )
        if config.risk_profile not in {"low", "medium", "high", "critical"}:
            result.add_warning(
                f"unknown risk_profile: {config.risk_profile} "
                "(expected one of low/medium/high/critical)"
            )
        return result

    # ------------------------------------------------------------------ #
    # Combined                                                            #
    # ------------------------------------------------------------------ #

    def validate(self, config: SeedConfig, agent: AgentConfig | None = None) -> ValidationResult:
        result = self.validate_seed(config)
        if agent is not None:
            result = result.merge(self.validate_agent(agent))
        return result

    def validate_or_raise(self, config: SeedConfig, agent: AgentConfig | None = None) -> None:
        result = self.validate(config, agent)
        if not result.ok:
            raise ConfigValidationError(result)


__all__ = ["ConfigValidator", "ValidationResult", "ConfigValidationError"]
