"""Unit tests for the config subpackage (F2-I)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from albra_seed.config import (
    ConfigLoader,
    ConfigManager,
    ConfigValidator,
    SecretsAdapter,
    SecretsRedactor,
    redact_secret_values,
)
from albra_seed.config.validator import ConfigValidationError
from albra_seed.core.config import AgentConfig, SeedConfig
from albra_seed.security.secrets import EnvSecretsManager


# =====================================================================
# Helper
# =====================================================================


def _clear_albra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every ALBRA_* env var so tests start from a clean baseline."""
    for k in list(os.environ):
        if k.startswith("ALBRA_"):
            monkeypatch.delenv(k, raising=False)
    for k in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
        "ZAI_API_KEY", "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)


# =====================================================================
# ConfigManager loads from env
# =====================================================================


def test_config_manager_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """ConfigManager.load() picks up env vars (ALBRA_* prefix + provider keys)."""
    _clear_albra_env(monkeypatch)
    monkeypatch.setenv("ALBRA_API_PORT", "9999")
    monkeypatch.setenv("ALBRA_DEFAULT_MODEL", "env-model")
    monkeypatch.setenv("ALBRA_MEMORY_MODE", "hybrid")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "a" * 50)

    mgr = ConfigManager()
    result = mgr.load()
    assert result.source == "env"
    assert mgr.seed.api_port == 9999
    assert mgr.seed.default_model == "env-model"
    assert mgr.seed.memory_mode.value == "hybrid"
    assert mgr.seed.openai_api_key == "sk-" + "a" * 50
    # is_core_only must be False now that openai_api_key is set.
    assert mgr.seed.is_core_only() is False


# =====================================================================
# ConfigValidator rejects invalid input
# =====================================================================


def test_config_validator_rejects_invalid() -> None:
    """ConfigValidator flags port out of range, negative sessions, etc."""
    validator = ConfigValidator()
    bad = SeedConfig.model_construct(api_port=99999, max_concurrent_sessions=-1)
    result = validator.validate_seed(bad)
    assert result.ok is False
    assert any("api_port" in e for e in result.errors)
    assert any("max_concurrent_sessions" in e for e in result.errors)


def test_config_validator_warns_short_api_keys() -> None:
    """Short API keys produce a warning (not an error)."""
    validator = ConfigValidator()
    bad = SeedConfig.model_construct(openai_api_key="short")
    result = validator.validate_seed(bad)
    assert result.ok is True  # warnings don't fail validation
    assert any("openai_api_key" in w for w in result.warnings)


def test_config_validator_raises_on_invalid() -> None:
    """ConfigValidator.validate_or_raise raises ConfigValidationError."""
    validator = ConfigValidator()
    bad = SeedConfig.model_construct(api_port=0, default_temperature=5.0)
    with pytest.raises(ConfigValidationError):
        validator.validate_or_raise(bad)


# =====================================================================
# ConfigManager save + reload
# =====================================================================


def test_config_manager_save_and_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ConfigManager.save() writes JSON to disk; reload() re-reads it."""
    _clear_albra_env(monkeypatch)
    monkeypatch.setenv("ALBRA_API_PORT", "7777")

    mgr = ConfigManager()
    mgr.load()
    assert mgr.seed.api_port == 7777

    out = tmp_path / "config.json"
    mgr.save(out, include_secrets=False)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["api_port"] == 7777

    # Reload from the file via the JSON loader.
    other = ConfigManager()
    result = other.load(out)
    assert result.source == "json"
    assert other.seed.api_port == 7777


def test_config_manager_save_strips_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default save() strips secrets; include_secrets=True preserves them."""
    _clear_albra_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value-12345678901234567890")

    mgr = ConfigManager()
    mgr.load()
    out_safe = tmp_path / "safe.json"
    mgr.save(out_safe, include_secrets=False)
    safe = json.loads(out_safe.read_text())
    assert safe["openai_api_key"] == "<redacted>"

    out_full = tmp_path / "full.json"
    mgr.save(out_full, include_secrets=True)
    full = json.loads(out_full.read_text())
    assert full["openai_api_key"] == "sk-secret-value-12345678901234567890"


# =====================================================================
# Secrets redacted in output
# =====================================================================


def test_secrets_redacted_in_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """redacted_snapshot() replaces every secret value with '<redacted>'."""
    _clear_albra_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-attempt-12345678901234567890")
    monkeypatch.setenv("ALBRA_API_PORT", "8888")

    mgr = ConfigManager()
    mgr.load()
    snap = mgr.redacted_snapshot()
    # Secret string values are redacted.
    assert snap["seed"]["openai_api_key"] == "<redacted>"
    # Non-secret values remain intact.
    assert snap["seed"]["api_port"] == 8888
    # The secrets snapshot contains the key name with an is_set bool — the
    # boolean is preserved (not redacted) because it's not a string.
    assert "OPENAI_API_KEY" in snap["secrets"]
    assert snap["secrets"]["OPENAI_API_KEY"] is True
    # Plain text dump must not contain the actual secret value.
    assert "sk-leak-attempt" not in mgr.to_redacted_json()


# =====================================================================
# SecretsRedactor — extra edge-case tests
# =====================================================================


def test_secrets_redactor_preserves_non_string_secret_values() -> None:
    """When a secret-named key holds a non-string (bool/int/None), the
    value is preserved — secrets are string-typed by definition."""
    redactor = SecretsRedactor()
    payload = {
        "openai_api_key": None,
        "vault_token": True,
        "api_port": 9999,
        "config": {"another_api_key": 42},
    }
    out = redactor.redact(payload)
    # Non-string secret-named values are preserved.
    assert out["openai_api_key"] is None
    assert out["vault_token"] is True
    assert out["config"]["another_api_key"] == 42
    # Real string secrets are redacted.
    payload2 = {"openai_api_key": "sk-real-secret"}
    assert redactor.redact(payload2)["openai_api_key"] == "<redacted>"


def test_secrets_redactor_avoids_false_positives() -> None:
    """Words containing 'token'/'secret' substrings are NOT redacted
    unless they're delimited by word boundaries."""
    redactor = SecretsRedactor()
    payload = {
        "default_max_tokens": 2048,           # not a token
        "secrets_backend": "env",             # not a secret value
        "tokens_used": 12,                    # not a token
        "log_path": "./logs/agent.log",       # not auth-related
    }
    out = redactor.redact(payload)
    assert out == payload  # untouched


def test_secrets_adapter_returns_only_is_set_flags() -> None:
    """SecretsAdapter.redacted_snapshot() returns {name: bool} — NEVER values."""
    manager = EnvSecretsManager()
    adapter = SecretsAdapter(manager=manager)
    snap = adapter.redacted_snapshot()
    assert isinstance(snap, dict)
    for k, v in snap.items():
        assert isinstance(k, str)
        assert isinstance(v, bool)


def test_config_loader_loads_from_json(tmp_path: Path) -> None:
    """ConfigLoader.load_from_json() reads a JSON config file."""
    p = tmp_path / "c.json"
    p.write_text(
        json.dumps({
            "api_port": 4242,
            "default_model": "json-model",
            "default_provider": "mock",
        }),
        encoding="utf-8",
    )
    loader = ConfigLoader()
    result = loader.load_from_json(p)
    assert result.source == "json"
    assert result.config.api_port == 4242
    assert result.config.default_model == "json-model"


def test_config_manager_get_set_roundtrip() -> None:
    """ConfigManager.get/set supports both attribute and dot-path access."""
    mgr = ConfigManager()
    assert mgr.get("api_port") == mgr.seed.api_port
    mgr.set("api_port", 65000, persist=True)
    assert mgr.seed.api_port == 65000
    assert mgr.get("api_port") == 65000
    # Override without persist goes into _overrides only.
    mgr.set("api_port", 11111)
    assert mgr.get("api_port") == 11111
    assert mgr.seed.api_port == 65000  # unchanged
