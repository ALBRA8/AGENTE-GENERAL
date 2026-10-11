"""Prompt template manager.

Loads, stores, and renders text prompt templates. Built-in templates
ship as .txt files in `templates/`. The manager also accepts runtime
overrides via `register()`.

No domain-specific templates live here. The SEED only ships generic
agent-infrastructure templates (system base, task decomposition, tool
selection). Specialization layers add domain prompts by registering
their own templates with this manager.
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Any

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Built-in template names (no domain knowledge).
BUILTIN_TEMPLATES: tuple[str, ...] = (
    "system_base",
    "task_decomposition",
    "tool_selection",
)


class PromptManager:
    """Store + render prompt templates."""

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._templates_dir: Path = templates_dir or TEMPLATES_DIR
        self._templates: dict[str, str] = {}
        self._load_builtins()

    # ------------------------------------------------------------------ #
    # Loading                                                            #
    # ------------------------------------------------------------------ #

    def _load_builtins(self) -> None:
        if not self._templates_dir.is_dir():
            return
        for name in BUILTIN_TEMPLATES:
            path = self._templates_dir / f"{name}.txt"
            if path.is_file():
                self._templates[name] = path.read_text(encoding="utf-8")

    def register(self, name: str, template: str, *, override: bool = False) -> None:
        """Register a new template. Built-ins can only be overridden if
        `override=True` is passed (avoids accidental clobbering)."""
        if name in BUILTIN_TEMPLATES and not override:
            raise ValueError(
                f"cannot override built-in template '{name}' without override=True"
            )
        self._templates[name] = template

    # ------------------------------------------------------------------ #
    # Rendering                                                          #
    # ------------------------------------------------------------------ #

    def render(self, name: str, variables: dict[str, Any] | None = None) -> str:
        """Render the template named `name` with `variables`.

        Uses `string.Template` ($var / ${var}) substitution. Missing
        variables are left as-is (no KeyError raised) — callers are
        responsible for validating output.
        """
        if name not in self._templates:
            raise KeyError(f"template not found: {name}")
        raw = self._templates[name]
        if not variables:
            return raw
        return Template(raw).safe_substitute(**variables)

    def get(self, name: str) -> str | None:
        return self._templates.get(name)

    def list_templates(self) -> list[str]:
        return sorted(self._templates.keys())

    def has(self, name: str) -> bool:
        return name in self._templates

    def snapshot(self) -> dict[str, Any]:
        return {
            "templates": self.list_templates(),
            "count": len(self._templates),
            "dir": str(self._templates_dir),
        }


__all__ = ["PromptManager", "BUILTIN_TEMPLATES"]
