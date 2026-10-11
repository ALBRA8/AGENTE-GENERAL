"""PluginManager — register/load/validate/enable/disable/version/health_check.

A plugin contributes tools/skills/providers/interfaces WITHOUT modifying
core. The PluginManager:
  - registers PluginManifest objects
  - runs the PluginValidator before activation
  - tracks enabled/disabled state
  - performs health checks via the manifest's health_check_endpoint
  - resolves dependency ordering (plugin A depends on plugin B)

Plugins do NOT modify core: they declare contributions that other
subsystems (ToolRegistry, SkillRegistry, ...) pick up.
"""

from __future__ import annotations

import asyncio
import importlib
from dataclasses import dataclass, field
from typing import Any

from albra_seed.plugins.loader import DiscoveredPlugin, PluginLoader
from albra_seed.plugins.manifest import PluginManifest
from albra_seed.plugins.validator import PluginValidationResult, PluginValidator


# =====================================================================
# Errors
# =====================================================================


class PluginAlreadyRegisteredError(KeyError):
    pass


class PluginNotFoundError(KeyError):
    pass


class PluginValidationError(RuntimeError):
    pass


# =====================================================================
# Record
# =====================================================================


@dataclass
class PluginRecord:
    manifest: PluginManifest
    enabled: bool = False
    validated: bool = False
    health: str = "unknown"
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# =====================================================================
# PluginManager
# =====================================================================


class PluginManager:
    """Manages plugin lifecycle: register → validate → enable → health_check."""

    def __init__(
        self,
        *,
        validator: PluginValidator | None = None,
        loader: PluginLoader | None = None,
    ) -> None:
        self._plugins: dict[str, PluginRecord] = {}
        self._validator = validator or PluginValidator()
        self._loader = loader or PluginLoader()

    # ------------------------------------------------------------------
    # Register / unregister
    # ------------------------------------------------------------------
    def register(
        self,
        manifest: PluginManifest,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
        validate: bool = True,
    ) -> PluginValidationResult:
        if manifest.name in self._plugins:
            raise PluginAlreadyRegisteredError(manifest.name)
        # Refresh validator's view of installed plugins so dependency
        # checks reflect the current registry state.
        self._validator.set_installed_plugins(set(self._plugins.keys()))

        result = PluginValidationResult(valid=True)
        if validate:
            result = self._validator.validate(manifest)
            if not result.valid:
                # do not activate, but keep a record so the user can see why
                self._plugins[manifest.name] = PluginRecord(
                    manifest=manifest,
                    enabled=False,
                    validated=False,
                    health="invalid",
                    source=source,
                    metadata=dict(metadata or {}),
                )
                return result

        self._plugins[manifest.name] = PluginRecord(
            manifest=manifest,
            enabled=False,
            validated=True,
            health="not_configured",
            source=source,
            metadata=dict(metadata or {}),
        )
        return result

    def unregister(self, name: str) -> bool:
        rec = self._plugins.pop(name, None)
        if rec is None:
            return False
        # Removing a plugin that other plugins depend on is forbidden.
        dependents = [
            n for n, r in self._plugins.items() if name in r.manifest.depends_on_plugins
        ]
        if dependents:
            # restore + raise
            self._plugins[name] = rec
            raise PluginValidationError(
                f"cannot unregister '{name}': required by {dependents}"
            )
        return True

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get(self, name: str) -> PluginManifest:
        return self._require(name).manifest

    def list(self) -> list[str]:
        return list(self._plugins.keys())

    def list_enabled(self) -> list[str]:
        return [n for n, r in self._plugins.items() if r.enabled]

    def is_enabled(self, name: str) -> bool:
        rec = self._plugins.get(name)
        return bool(rec and rec.enabled)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                **r.manifest.to_summary(),
                "validated": r.validated,
                "health": r.health,
                "source": r.source,
            }
            for n, r in self._plugins.items()
        ]

    # ------------------------------------------------------------------
    # Enable / disable
    # ------------------------------------------------------------------
    def enable(self, name: str) -> None:
        rec = self._require(name)
        if not rec.validated:
            raise PluginValidationError(f"plugin '{name}' is not validated")
        # ensure dependencies are enabled first
        for dep in rec.manifest.depends_on_plugins:
            if not self.is_enabled(dep):
                raise PluginValidationError(
                    f"plugin '{name}' requires enabled plugin '{dep}'"
                )
        rec.enabled = True
        rec.health = "healthy"

    def disable(self, name: str) -> None:
        rec = self._require(name)
        rec.enabled = False
        rec.health = "disabled"
        # disable plugins that depend on this one too (transitive)
        for n, r in self._plugins.items():
            if name in r.manifest.depends_on_plugins and r.enabled:
                r.enabled = False
                r.health = "disabled_upstream"

    # ------------------------------------------------------------------
    # Version
    # ------------------------------------------------------------------
    def version(self, name: str) -> str:
        return self._require(name).manifest.version

    def upgrade(self, name: str, manifest: PluginManifest) -> None:
        if manifest.name != name:
            raise PluginValidationError(
                f"upgrade manifest name mismatch: {manifest.name} != {name}"
            )
        rec = self._require(name)
        result = self._validator.validate(manifest)
        if not result.valid:
            raise PluginValidationError(
                f"upgrade failed validation: {result.errors}"
            )
        was_enabled = rec.enabled
        rec.manifest = manifest
        rec.validated = True
        rec.enabled = was_enabled
        rec.health = "healthy" if was_enabled else "not_configured"

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    async def health_check(self, name: str) -> str:
        rec = self._require(name)
        if not rec.enabled:
            rec.health = "disabled"
            return rec.health
        ep = rec.manifest.health_check_entrypoint
        if not ep:
            rec.health = "healthy"
            return rec.health
        try:
            module_path, _, attr = ep.partition(":")
            module = importlib.import_module(module_path)
            fn = getattr(module, attr) if attr else module
            if callable(fn):
                result = fn()
                if asyncio.iscoroutine(result):
                    result = await result
                rec.health = "healthy" if bool(result) else "degraded"
            else:
                rec.health = "not_configured"
        except Exception:
            rec.health = "error"
        return rec.health

    async def health_check_all(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in self._plugins.keys():
            out[name] = await self.health_check(name)
        return out

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def load_from_directory(self, root: str) -> list[str]:
        """Discover + register all plugins under `root`."""
        result = self._loader.discover(root)
        registered: list[str] = []
        for disc in result.discovered:
            try:
                vres = self.register(
                    disc.manifest,
                    source=str(disc.path),
                    validate=True,
                )
                if vres.valid:
                    registered.append(disc.manifest.name)
            except PluginAlreadyRegisteredError:
                continue
        return registered

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _require(self, name: str) -> PluginRecord:
        rec = self._plugins.get(name)
        if rec is None:
            raise PluginNotFoundError(name)
        return rec
