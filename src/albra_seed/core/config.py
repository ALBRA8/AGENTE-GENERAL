"""Centralized configuration for ALBRA AGENT SEED V1.0.

Layers (strictly separated):
    DEFAULT CONFIG   — built-in defaults, never modified at runtime
    ENV CONFIG        — loaded from .env / environment variables
    RUNTIME CONFIG    — passed at agent initialization
    SECRET CONFIG     — secrets isolated in a SecretsManager, never logged
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.constants import (
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    DEFAULT_AUTONOMY_LEVEL,
    DEFAULT_LOG_FORMAT,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_CONCURRENT_SESSIONS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MEMORY_BACKEND,
    DEFAULT_MEMORY_MODE,
    DEFAULT_MEMORY_PATH,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    DEFAULT_SESSION_TTL_SECONDS,
    DEFAULT_TEMPERATURE,
    SEED_VERSION,
)


class MemoryMode(str, Enum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    HYBRID = "hybrid"


class SecretsBackend(str, Enum):
    ENV = "env"
    FILE = "file"
    VAULT = "vault"


class LogFormat(str, Enum):
    JSON = "json"
    CONSOLE = "console"


class SeedConfig(BaseModel):
    """Top-level configuration for the SEED itself (not specialized agents)."""

    seed_version: str = SEED_VERSION
    default_provider: str = DEFAULT_PROVIDER
    default_model: str = DEFAULT_MODEL
    default_temperature: float = DEFAULT_TEMPERATURE
    default_max_tokens: int = DEFAULT_MAX_TOKENS

    memory_mode: MemoryMode = DEFAULT_MEMORY_MODE
    memory_backend: str = DEFAULT_MEMORY_BACKEND
    memory_path: str = DEFAULT_MEMORY_PATH
    vector_backend: str = "none"
    vector_dimension: int = 1536

    guardrails_enabled: bool = True
    audit_log_path: str = "./logs/audit.jsonl"
    secrets_backend: SecretsBackend = SecretsBackend.ENV
    vault_url: str | None = None
    vault_token: str | None = None

    log_level: str = DEFAULT_LOG_LEVEL
    log_format: LogFormat = DEFAULT_LOG_FORMAT
    log_path: str = "./logs/agent.log"
    metrics_enabled: bool = True
    tracing_enabled: bool = False
    otel_endpoint: str | None = None

    api_host: str = DEFAULT_API_HOST
    api_port: int = DEFAULT_API_PORT
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    max_concurrent_sessions: int = DEFAULT_MAX_CONCURRENT_SESSIONS
    default_autonomy_level: int = DEFAULT_AUTONOMY_LEVEL

    # External providers — always OPTIONAL
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    groq_api_key: str | None = None
    zai_api_key: str | None = None
    ollama_base_url: str | None = None

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> SeedConfig:
        """Load configuration from environment variables (and optionally .env)."""
        if env_path:
            from dotenv import load_dotenv

            load_dotenv(env_path)

        def _get(key: str, default: Any = None) -> Any:
            return os.environ.get(f"ALBRA_{key.upper()}", default)

        def _getbool(key: str, default: bool) -> bool:
            val = os.environ.get(f"ALBRA_{key.upper()}")
            if val is None:
                return default
            return val.lower() in {"1", "true", "yes", "on"}

        def _getint(key: str, default: int) -> int:
            val = os.environ.get(f"ALBRA_{key.upper()}")
            return int(val) if val is not None else default

        def _getfloat(key: str, default: float) -> float:
            val = os.environ.get(f"ALBRA_{key.upper()}")
            return float(val) if val is not None else default

        return cls(
            default_provider=_get("DEFAULT_PROVIDER", DEFAULT_PROVIDER),
            default_model=_get("DEFAULT_MODEL", DEFAULT_MODEL),
            default_temperature=_getfloat("DEFAULT_TEMPERATURE", DEFAULT_TEMPERATURE),
            default_max_tokens=_getint("DEFAULT_MAX_TOKENS", DEFAULT_MAX_TOKENS),
            memory_mode=MemoryMode(_get("MEMORY_MODE", DEFAULT_MEMORY_MODE)),
            memory_backend=_get("MEMORY_BACKEND", DEFAULT_MEMORY_BACKEND),
            memory_path=_get("MEMORY_PATH", DEFAULT_MEMORY_PATH),
            vector_backend=_get("VECTOR_BACKEND", "none"),
            vector_dimension=_getint("VECTOR_DIMENSION", 1536),
            guardrails_enabled=_getbool("GUARDRAILS_ENABLED", True),
            audit_log_path=_get("AUDIT_LOG_PATH", "./logs/audit.jsonl"),
            secrets_backend=SecretsBackend(_get("SECRETS_BACKEND", "env")),
            vault_url=_get("VAULT_URL"),
            vault_token=_get("VAULT_TOKEN"),
            log_level=_get("LOG_LEVEL", DEFAULT_LOG_LEVEL),
            log_format=LogFormat(_get("LOG_FORMAT", "json")),
            log_path=_get("LOG_PATH", "./logs/agent.log"),
            metrics_enabled=_getbool("METRICS_ENABLED", True),
            tracing_enabled=_getbool("TRACING_ENABLED", False),
            otel_endpoint=_get("OTEL_ENDPOINT"),
            api_host=_get("API_HOST", DEFAULT_API_HOST),
            api_port=_getint("API_PORT", DEFAULT_API_PORT),
            session_ttl_seconds=_getint("SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS),
            max_concurrent_sessions=_getint("MAX_CONCURRENT_SESSIONS", DEFAULT_MAX_CONCURRENT_SESSIONS),
            default_autonomy_level=_getint("DEFAULT_AUTONOMY_LEVEL", DEFAULT_AUTONOMY_LEVEL),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            groq_api_key=os.environ.get("GROQ_API_KEY"),
            zai_api_key=os.environ.get("ZAI_API_KEY"),
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL"),
        )

    def is_core_only(self) -> bool:
        """True when no external providers are configured. The SEED MUST be
        fully functional in this mode (Mock provider used as fallback)."""
        return not any(
            [
                self.openai_api_key,
                self.anthropic_api_key,
                self.groq_api_key,
                self.zai_api_key,
                self.ollama_base_url,
            ]
        )


class AgentConfig(BaseModel):
    """Per-agent runtime configuration. Used by Agent factory."""

    name: str = "albra-agent"
    agent_id: str | None = None  # auto-generated if None
    autonomy_level: int = DEFAULT_AUTONOMY_LEVEL
    risk_profile: str = "low"
    permissions: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    memory_domains: list[str] = Field(default_factory=lambda: ["default"])
    interfaces: list[str] = Field(default_factory=lambda: ["text"])
    dependencies: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
