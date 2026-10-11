"""Unit tests for the plugins subpackage."""

import asyncio
import json
from pathlib import Path

import pytest

from albra_seed.core.types import RiskLevel
from albra_seed.plugins import (
    PluginLoader,
    PluginManager,
    PluginManifest,
    PluginValidationError,
    PluginValidator,
)


def _manifest(**overrides) -> PluginManifest:
    base = dict(
        name="demo_plugin",
        version="1.0.0",
        description="A demo plugin.",
        contributes_tools=["demo_tool"],
        contributes_skills=["demo_skill"],
        risk_level=RiskLevel.LOW,
    )
    base.update(overrides)
    return PluginManifest(**base)


# -------------------------------------------------------------------
# 1. Manager register + enable
# -------------------------------------------------------------------


def test_plugin_manager_register_and_enable():
    pm = PluginManager()
    manifest = _manifest()
    vres = pm.register(manifest)
    assert vres.valid
    assert "demo_plugin" in pm.list()
    assert pm.is_enabled("demo_plugin") is False

    pm.enable("demo_plugin")
    assert pm.is_enabled("demo_plugin") is True

    pm.disable("demo_plugin")
    assert pm.is_enabled("demo_plugin") is False


# -------------------------------------------------------------------
# 2. Validator rejects missing manifest / invalid manifest
# -------------------------------------------------------------------


def test_plugin_validator_rejects_invalid_manifest():
    pm = PluginManager()
    # No contributions declared
    bad = PluginManifest(
        name="empty",
        version="1.0.0",
        description="contributes nothing",
        contributes_tools=[],
        contributes_skills=[],
        contributes_providers=[],
        contributes_interfaces=[],
    )
    vres = pm.register(bad)
    assert not vres.valid
    assert any("contributes nothing" in e for e in vres.errors)
    assert pm.is_enabled("empty") is False


# -------------------------------------------------------------------
# 3. Loader discovers plugins from temp dir
# -------------------------------------------------------------------


def test_plugin_loader_discovers_plugins(tmp_path: Path):
    # Build a tiny plugin tree on disk
    plugin_dir = tmp_path / "demo_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "demo_plugin",
                "version": "1.0.0",
                "description": "discovered plugin",
                "contributes_tools": ["demo_tool"],
                "risk_level": "low",
            }
        )
    )

    # A second plugin at root level
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "name": "root_plugin",
                "version": "0.1.0",
                "description": "root-level plugin",
                "contributes_skills": ["root_skill"],
                "risk_level": "low",
            }
        )
    )

    loader = PluginLoader()
    result = loader.discover(tmp_path)
    assert not result.errors
    discovered_names = {d.manifest.name for d in result.discovered}
    assert "demo_plugin" in discovered_names
    assert "root_plugin" in discovered_names

    # End-to-end: register them all via PluginManager
    pm = PluginManager()
    for disc in result.discovered:
        vres = pm.register(disc.manifest, source=str(disc.path))
        assert vres.valid, (disc.manifest.name, vres.errors)
    assert set(pm.list()) >= {"demo_plugin", "root_plugin"}


# -------------------------------------------------------------------
# 4. Plugin can be disabled (and dependents cascade)
# -------------------------------------------------------------------


def test_plugin_can_be_disabled_and_cascades():
    pm = PluginManager()
    base = _manifest(name="base_plugin", contributes_tools=["base_tool"])
    dependent = _manifest(
        name="dependent_plugin",
        contributes_tools=["dep_tool"],
        depends_on_plugins=["base_plugin"],
    )
    pm.register(base)
    pm.register(dependent)

    pm.enable("base_plugin")
    pm.enable("dependent_plugin")
    assert pm.is_enabled("dependent_plugin") is True

    # Disable base -> dependent should cascade-disable
    pm.disable("base_plugin")
    assert pm.is_enabled("base_plugin") is False
    assert pm.is_enabled("dependent_plugin") is False
    rec = pm.get("dependent_plugin")  # manifest is accessible
    assert rec.name == "dependent_plugin"


# -------------------------------------------------------------------
# 5. Validator rejects plugin with missing plugin dependency
# -------------------------------------------------------------------


def test_plugin_validator_rejects_missing_plugin_dependency():
    pm = PluginManager()
    manifest = _manifest(
        name="needs_missing",
        contributes_tools=["x"],
        depends_on_plugins=["does_not_exist"],
    )
    vres = pm.register(manifest)
    assert not vres.valid
    assert any("does_not_exist" in e for e in vres.errors)


# -------------------------------------------------------------------
# 6. Manager refuses to unregister plugin that has dependents
# -------------------------------------------------------------------


def test_plugin_manager_refuses_unregister_with_dependents():
    pm = PluginManager()
    pm.register(_manifest(name="base_plugin", contributes_tools=["base_tool"]))
    pm.register(
        _manifest(
            name="dependent_plugin",
            contributes_tools=["dep_tool"],
            depends_on_plugins=["base_plugin"],
        )
    )
    with pytest.raises(PluginValidationError):
        pm.unregister("base_plugin")
    # But unregistering the dependent is fine
    assert pm.unregister("dependent_plugin") is True
    assert "dependent_plugin" not in pm.list()


# -------------------------------------------------------------------
# 7. Health check returns healthy for enabled plugin with no endpoint
# -------------------------------------------------------------------


def test_plugin_health_check_no_endpoint_returns_healthy():
    pm = PluginManager()
    pm.register(_manifest(name="demo_plugin", contributes_tools=["demo_tool"]))
    pm.enable("demo_plugin")
    health = asyncio.run(pm.health_check("demo_plugin"))
    assert health == "healthy"


# -------------------------------------------------------------------
# 8. Upgrade updates version
# -------------------------------------------------------------------


def test_plugin_upgrade_updates_version():
    pm = PluginManager()
    pm.register(_manifest(name="demo_plugin", version="1.0.0", contributes_tools=["demo_tool"]))
    pm.enable("demo_plugin")
    new_manifest = _manifest(name="demo_plugin", version="2.0.0", contributes_tools=["demo_tool"])
    pm.upgrade("demo_plugin", new_manifest)
    assert pm.version("demo_plugin") == "2.0.0"
    assert pm.is_enabled("demo_plugin") is True
