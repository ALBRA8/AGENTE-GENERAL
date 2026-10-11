"""PluginValidator — validates a PluginManifest before activation.

Checks:
  - manifest fields are populated
  - dependencies on other plugins can be resolved
  - external package dependencies are installed
  - at least one entry point OR contribution is declared
  - health check endpoint (if specified) is a callable or dotted path
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

from albra_seed.plugins.manifest import PluginManifest


# =====================================================================
# Result
# =====================================================================


@dataclass
class PluginValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.valid


# =====================================================================
# PluginValidator
# =====================================================================


class PluginValidator:
    """Validates a PluginManifest + dependencies before activation."""

    def __init__(
        self,
        *,
        installed_plugins: set[str] | None = None,
        check_packages: bool = True,
    ) -> None:
        self.installed_plugins = set(installed_plugins or [])
        self.check_packages = check_packages

    # ------------------------------------------------------------------
    def set_installed_plugins(self, names: set[str]) -> None:
        self.installed_plugins = set(names)

    def validate(self, manifest: PluginManifest) -> PluginValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        # --- basic fields ---
        if not manifest.name:
            errors.append("manifest.name is required")
        if not manifest.version:
            errors.append("manifest.version is required")
        if not manifest.description:
            warnings.append("manifest.description is empty")

        # --- contributions / entry points ---
        if not manifest.contributes_anything():
            errors.append(
                "plugin contributes nothing (no tools/skills/providers/interfaces/capabilities)"
            )

        # --- plugin dependencies ---
        missing_plugins = [
            d for d in manifest.depends_on_plugins if d not in self.installed_plugins
        ]
        if missing_plugins:
            errors.append(f"missing plugin dependencies: {missing_plugins}")

        # --- package dependencies ---
        if self.check_packages:
            for pkg in manifest.depends_on_packages:
                if not _is_package_installed(pkg):
                    errors.append(f"missing python package dependency: {pkg}")

        # --- entry points must be importable (best effort) ---
        for field_name in (
            "tools_entrypoint",
            "skills_entrypoint",
            "providers_entrypoint",
            "interfaces_entrypoint",
            "health_check_entrypoint",
        ):
            ep = getattr(manifest, field_name, None)
            if ep and not _is_entrypoint_importable(ep):
                errors.append(f"unresolvable entry point {field_name}={ep!r}")

        # --- risk level sanity ---
        if manifest.risk_level.value == "unknown":
            errors.append("risk_level must not be 'unknown'")

        return PluginValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
        )


# =====================================================================
# Helpers
# =====================================================================


def _is_package_installed(package: str) -> bool:
    """Check if a python package is importable. Top-level name only."""
    try:
        importlib.import_module(package.split(".")[0])
        return True
    except Exception:
        return False


def _is_entrypoint_importable(entrypoint: str) -> bool:
    """`module.sub:attr` -> importable."""
    if ":" in entrypoint:
        module_path, _, _attr = entrypoint.partition(":")
    else:
        module_path = entrypoint
    try:
        importlib.import_module(module_path)
        return True
    except Exception:
        return False
