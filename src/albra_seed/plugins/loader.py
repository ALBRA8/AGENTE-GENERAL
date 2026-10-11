"""PluginLoader — discovers plugins from a directory.

Discovery strategy:
  1. Scan a directory for `manifest.json` (or `plugin.json`) files.
  2. Parse each into a PluginManifest.
  3. Optionally load the entry point module to discover contributions.

This is intentionally simple (no entry-points metadata): files are
discovered by name pattern, which makes the loader work in any
deployment that has the directory on disk.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from albra_seed.plugins.manifest import PluginManifest


# =====================================================================
# Result
# =====================================================================


@dataclass
class DiscoveredPlugin:
    manifest: PluginManifest
    path: Path
    errors: list[str] = field(default_factory=list)


@dataclass
class LoaderResult:
    discovered: list[DiscoveredPlugin] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and all(not d.errors for d in self.discovered)


# =====================================================================
# PluginLoader
# =====================================================================


MANIFEST_FILENAMES = ("manifest.json", "plugin.json")


class PluginLoader:
    """Discovers plugins by scanning a directory for manifest files."""

    def __init__(self, *, search_subdirs: bool = True) -> None:
        self.search_subdirs = search_subdirs

    # ------------------------------------------------------------------
    def discover(self, root: str | Path) -> LoaderResult:
        root_path = Path(root)
        if not root_path.exists():
            return LoaderResult(errors=[f"plugin root does not exist: {root_path}"])
        if not root_path.is_dir():
            return LoaderResult(errors=[f"plugin root is not a directory: {root_path}"])

        discovered: list[DiscoveredPlugin] = []
        errors: list[str] = []

        # Direct manifest files at root
        for fname in MANIFEST_FILENAMES:
            for path in root_path.glob(fname):
                disc = self._load_manifest(path)
                if disc:
                    discovered.append(disc)

        # Subdirectory plugins (one manifest per subdir)
        if self.search_subdirs:
            for sub in sorted(root_path.iterdir()):
                if not sub.is_dir():
                    continue
                if sub.name.startswith("__"):
                    continue
                for fname in MANIFEST_FILENAMES:
                    path = sub / fname
                    if path.exists():
                        disc = self._load_manifest(path)
                        if disc:
                            discovered.append(disc)
                        break

        return LoaderResult(discovered=discovered, errors=errors)

    # ------------------------------------------------------------------
    def load_manifest_from_dict(self, data: dict[str, Any]) -> PluginManifest | None:
        try:
            return PluginManifest(**data)
        except Exception:
            return None

    # ------------------------------------------------------------------
    def _load_manifest(self, path: Path) -> DiscoveredPlugin | None:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            return DiscoveredPlugin(
                manifest=_placeholder_manifest(path.parent.name),
                path=path,
                errors=[f"failed to parse {path}: {exc}"],
            )
        try:
            manifest = PluginManifest(**data)
        except Exception as exc:
            return DiscoveredPlugin(
                manifest=_placeholder_manifest(data.get("name", path.parent.name) if isinstance(data, dict) else path.parent.name),
                path=path,
                errors=[f"invalid manifest at {path}: {exc}"],
            )
        return DiscoveredPlugin(manifest=manifest, path=path)

    # ------------------------------------------------------------------
    def load_entrypoint(self, manifest: PluginManifest) -> Any:
        """Import the module referenced by `tools_entrypoint` (or whichever
        entry point is set first). Returns the imported module, or None."""
        for ep in (
            manifest.tools_entrypoint,
            manifest.skills_entrypoint,
            manifest.providers_entrypoint,
            manifest.interfaces_entrypoint,
        ):
            if not ep:
                continue
            module_path = ep.split(":", 1)[0]
            try:
                return importlib.import_module(module_path)
            except Exception:
                continue
        return None


def _placeholder_manifest(name: str) -> PluginManifest:
    return PluginManifest(name=name, version="0.0.0-error", description="invalid manifest")
