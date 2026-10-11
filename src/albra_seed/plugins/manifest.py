"""PluginManifest — formal manifest model extending `Plugin`.

A plugin contributes tools/skills/providers/interfaces WITHOUT modifying
core. The manifest carries:
  - dependencies (other plugins / packages required)
  - health check endpoint (callable returning bool)
  - entry points (module paths for tools/skills/providers)
  - capability contributions (capabilities registered by the plugin)
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import Field, model_validator

from albra_seed.core.base import Plugin
from albra_seed.core.types import RiskLevel


class PluginManifest(Plugin):
    """Formal manifest describing what a plugin contributes."""

    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""
    min_seed_version: str = "1.0.0"

    # dependencies on other plugins (by name) or external packages
    depends_on_plugins: list[str] = Field(default_factory=list)
    depends_on_packages: list[str] = Field(default_factory=list)

    # entry points: dotted module paths to factory callables
    tools_entrypoint: str | None = None
    skills_entrypoint: str | None = None
    providers_entrypoint: str | None = None
    interfaces_entrypoint: str | None = None

    # health check (callable or dotted path)
    health_check_entrypoint: str | None = None

    # capabilities registered by the plugin
    contributes_capabilities: list[str] = Field(default_factory=list)

    risk_level: RiskLevel = RiskLevel.LOW

    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _validate_manifest(self) -> "PluginManifest":
        if not self.name:
            raise ValueError("PluginManifest.name must be set")
        if not self.version:
            raise ValueError("PluginManifest.version must be set")
        if not isinstance(self.depends_on_plugins, list):
            raise ValueError("depends_on_plugins must be a list")
        if not isinstance(self.depends_on_packages, list):
            raise ValueError("depends_on_packages must be a list")
        return self

    # ------------------------------------------------------------------
    def contributes_anything(self) -> bool:
        return bool(
            self.contributes_tools
            or self.contributes_skills
            or self.contributes_providers
            or self.contributes_interfaces
            or self.contributes_capabilities
        )

    def to_summary(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "version": self.version,
            "enabled": self.enabled,
            "description": self.description,
            "depends_on_plugins": list(self.depends_on_plugins),
            "depends_on_packages": list(self.depends_on_packages),
            "contributes": {
                "tools": list(self.contributes_tools),
                "skills": list(self.contributes_skills),
                "providers": list(self.contributes_providers),
                "interfaces": list(self.contributes_interfaces),
                "capabilities": list(self.contributes_capabilities),
            },
            "risk_level": self.risk_level.value,
        }
