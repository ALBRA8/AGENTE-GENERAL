"""Tests for the read-only HITL deployment smoke test."""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "hitl_acceptance_check.py"
SPEC = importlib.util.spec_from_file_location("hitl_acceptance_check", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_smoke_check_requires_base_url_and_token(monkeypatch, capsys):
    monkeypatch.delenv("ALBRA_HITL_BASE_URL", raising=False)
    monkeypatch.delenv("ALBRA_HITL_VALID_TOKEN", raising=False)
    assert MODULE.main() == 2
    assert "ALBRA_HITL_BASE_URL" in capsys.readouterr().out


def test_smoke_check_rejects_non_https_non_loopback(monkeypatch, capsys):
    monkeypatch.setenv("ALBRA_HITL_BASE_URL", "http://agent.example.com")
    monkeypatch.setenv("ALBRA_HITL_VALID_TOKEN", "test-only")
    assert MODULE.main() == 2
    assert "HTTPS is required" in capsys.readouterr().out


def test_smoke_check_never_prints_token_and_checks_rbac(monkeypatch, capsys):
    monkeypatch.setenv("ALBRA_HITL_BASE_URL", "https://agent.example.test")
    monkeypatch.setenv("ALBRA_HITL_VALID_TOKEN", "do-not-print-this-token")
    monkeypatch.setenv("ALBRA_HITL_NO_LIST_TOKEN", "no-list-token")
    statuses = iter([(401, ""), (401, ""), (200, '{"items": []}'), (403, "")])
    monkeypatch.setattr(MODULE, "request", lambda *args, **kwargs: next(statuses))

    assert MODULE.main() == 0
    output = capsys.readouterr().out
    assert "PASS: anonymous request denied" in output
    assert "PASS: valid principal without list permission denied" in output
    assert "do-not-print-this-token" not in output
    assert "no-list-token" not in output
