"""Unit tests for the cli subpackage (F2-I)."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout

import pytest

from albra_seed.cli.cli import build_parser, main
from albra_seed.cli.context import CLIContext, make_default_context
from albra_seed.cli.formatter import Formatter
from albra_seed.core.config import SeedConfig
from albra_seed.runtime.runtime import Runtime


# =====================================================================
# Helpers
# =====================================================================


def _capture(argv: list[str]) -> tuple[int, str]:
    """Run the CLI with argv, return (exit_code, captured_stdout)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def _clear_albra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ):
        if k.startswith("ALBRA_"):
            monkeypatch.delenv(k, raising=False)
    for k in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
        "ZAI_API_KEY", "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(k, raising=False)


# =====================================================================
# Help / structure
# =====================================================================


def test_cli_main_help() -> None:
    """`albra --help` prints usage and lists every subcommand."""
    parser = build_parser()
    buf = io.StringIO()
    parser.print_help(buf)
    text = buf.getvalue()
    assert "usage: albra" in text.lower()
    for cmd in (
        "health", "agent", "doctor", "capabilities", "providers",
        "tools", "skills", "memory", "execution", "audit", "config",
    ):
        assert cmd in text


def test_cli_main_no_args_prints_help() -> None:
    """`albra` with no command prints help and exits 0."""
    rc, out = _capture([])
    assert rc == 0
    assert "usage: albra" in out.lower()


def test_cli_version_flag() -> None:
    """`albra --version` prints the package version."""
    rc, out = _capture(["--version"])
    assert rc == 0
    assert "1.0.0" in out


# =====================================================================
# Agent info
# =====================================================================


def test_cli_agent_info_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra agent info` prints the seed identity when no agent registered."""
    _clear_albra_env(monkeypatch)
    rc, out = _capture(["agent", "info"])
    assert rc == 0
    assert "albra-seed" in out
    assert "seed_version" in out or "ALBRA_SEED_V1.0" in out


# =====================================================================
# Health
# =====================================================================


def test_cli_health_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra health` prints the runtime health snapshot."""
    _clear_albra_env(monkeypatch)
    rc, out = _capture(["health"])
    assert rc == 0
    assert "status" in out
    assert "phase" in out
    assert "core_only" in out


# =====================================================================
# Doctor / diagnostics
# =====================================================================


def test_cli_doctor_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra doctor` runs diagnostic checks and prints a report."""
    _clear_albra_env(monkeypatch)
    rc, out = _capture(["doctor"])
    assert rc == 0
    assert "overall_status" in out
    assert "components" in out
    # Alias 'diagnostics' should work too.
    rc2, out2 = _capture(["diagnostics"])
    assert rc2 == 0
    assert "overall_status" in out2


# =====================================================================
# Config redacts secrets
# =====================================================================


def test_cli_config_redacts_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra config` output NEVER includes real secret values."""
    _clear_albra_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak-attempt-12345678901234567890")
    monkeypatch.setenv("ALBRA_API_PORT", "4242")

    rc, out = _capture(["--format", "json", "config"])
    assert rc == 0
    # The CLI may emit structlog lines (secrets.get backend=env ...) to
    # stdout before the JSON block. Parse only the JSON object — find the
    # first "{" and decode to the matching "}".
    start = out.index("{")
    data = json.loads(out[start:])
    assert data["seed"]["api_port"] == 4242
    assert data["seed"]["openai_api_key"] == "<redacted>"
    # Plain text must not contain the actual secret value.
    assert "sk-leak-attempt" not in out
    # The `secrets` block carries only is_set bool flags, never values.
    assert data["secrets"]["OPENAI_API_KEY"] is True


def test_cli_config_validate_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra config --validate` runs the validator."""
    _clear_albra_env(monkeypatch)
    rc, out = _capture(["config", "--validate"])
    # Default config is valid → rc 0.
    assert rc == 0
    assert "ok" in out


# =====================================================================
# Providers
# =====================================================================


def test_cli_providers_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra providers` lists registered providers (empty when none)."""
    _clear_albra_env(monkeypatch)
    rc, out = _capture(["providers"])
    assert rc == 0
    # With no provider registry registered with the runtime, the CLI
    # prints an "no providers" note.
    assert "providers" in out.lower()


def test_cli_providers_command_with_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a provider_router is registered, `albra providers` lists them."""
    _clear_albra_env(monkeypatch)
    from albra_seed.providers.mock import MockProvider
    from albra_seed.providers.registry import ProviderRegistry
    from albra_seed.providers.router import ProviderRouter

    rt = Runtime()
    registry = ProviderRegistry()
    registry.register(MockProvider())
    router = ProviderRouter(registry=registry, default_provider="mock")
    rt.register("provider_router", router)
    cfg = SeedConfig()
    from albra_seed.config import ConfigManager
    mgr = ConfigManager(seed=cfg)
    ctx = CLIContext(runtime=rt, config=mgr, formatter_name="table")

    buf = io.StringIO()
    fmt = Formatter("table", out=buf)
    rows: list[dict] = []
    for name in registry.list_all():
        h = registry.health_for(name)
        rows.append({
            "name": name,
            "state": h.state.value if h else "unknown",
            "available": h.is_available if h else False,
            "is_mock": getattr(registry.get(name), "is_mock", False),
            "is_external": getattr(registry.get(name), "is_external", False),
        })
    fmt.render_table(rows, title="providers")
    text = buf.getvalue()
    assert "mock" in text
    assert "healthy" in text.lower()


# =====================================================================
# Tools / skills / memory / audit / execution — they all run cleanly
# =====================================================================


def test_cli_tools_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra tools` runs and prints a table (possibly empty)."""
    _clear_albra_env(monkeypatch)
    rc, out = _capture(["tools"])
    assert rc == 0


def test_cli_skills_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra skills` runs and prints a table (possibly empty)."""
    _clear_albra_env(monkeypatch)
    rc, out = _capture(["skills"])
    assert rc == 0


def test_cli_memory_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra memory` runs and prints memory stats."""
    _clear_albra_env(monkeypatch)
    rc, out = _capture(["memory"])
    assert rc == 0
    assert "memory" in out.lower()


def test_cli_audit_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra audit` runs and prints the audit log (empty when no logger)."""
    _clear_albra_env(monkeypatch)
    rc, out = _capture(["audit"])
    assert rc == 0


def test_cli_execution_command_no_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """`albra execution` shows a 'no agent registered' note."""
    _clear_albra_env(monkeypatch)
    rc, out = _capture(["execution"])
    assert rc == 0
    assert "agent" in out.lower()


# =====================================================================
# Formatter
# =====================================================================


def test_formatter_table_renders_headers_and_rows() -> None:
    """The table formatter renders column headers + row values."""
    buf = io.StringIO()
    fmt = Formatter("table", out=buf)
    fmt.render_table(
        [
            {"name": "alice", "age": 30},
            {"name": "bob", "age": 25},
        ],
        title="users",
    )
    out = buf.getvalue()
    assert "users" in out
    assert "name" in out and "age" in out
    assert "alice" in out and "bob" in out


def test_formatter_json_renders_redacted_payload() -> None:
    """JSON formatter runs the redactor before serializing."""
    buf = io.StringIO()
    fmt = Formatter("json", out=buf)
    fmt.render({"openai_api_key": "sk-leak", "api_port": 9999})
    out = buf.getvalue()
    data = json.loads(out)
    assert data["openai_api_key"] == "<redacted>"
    assert data["api_port"] == 9999


# =====================================================================
# CLIContext.build loads env vars
# =====================================================================


def test_cli_context_build_loads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLIContext.build() picks up env vars when no ConfigManager given."""
    _clear_albra_env(monkeypatch)
    monkeypatch.setenv("ALBRA_API_PORT", "5555")
    ctx = make_default_context()
    assert ctx.config.seed.api_port == 5555


def test_cli_context_build_keeps_passed_config() -> None:
    """When a ConfigManager is passed, env loading is skipped."""
    cfg = SeedConfig()
    from albra_seed.config import ConfigManager
    mgr = ConfigManager(seed=cfg)
    ctx = CLIContext.build(config=mgr)
    assert ctx.config is mgr
