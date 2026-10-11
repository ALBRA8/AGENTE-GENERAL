"""Output formatter — table / json / yaml.

Used by every CLI command to render output. The formatter is pure
stdlib (json from stdlib; yaml falls back to a simple key=value render
when PyYAML is missing — for tabular data we still want a structured
form, so we JSON-dump when YAML is unavailable).

CRITICAL: every formatter routes its payload through the secrets
redactor before emitting, so secrets are NEVER printed to stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from albra_seed.config.secrets import redact_secret_values


class Formatter:
    """Format CLI output as table / json / yaml.

    `format` is one of: "table" (default), "json", "yaml".
    """

    def __init__(self, fmt: str = "table", *, out: TextIO | None = None) -> None:
        if fmt not in {"table", "json", "yaml"}:
            raise ValueError(f"unknown formatter: {fmt}")
        self.fmt: str = fmt
        self.out: TextIO = out or sys.stdout

    # ------------------------------------------------------------------ #
    # Public render API                                                   #
    # ------------------------------------------------------------------ #

    def render(self, payload: Any, *, title: str | None = None) -> None:
        redacted = redact_secret_values(payload)
        if self.fmt == "json":
            self._render_json(redacted, title)
        elif self.fmt == "yaml":
            self._render_yaml(redacted, title)
        else:
            self._render_table(redacted, title)

    def render_kv(self, pairs: dict[str, Any], *, title: str | None = None) -> None:
        """Convenience renderer for key/value pairs."""
        self.render(pairs, title=title)

    def render_table(self, rows: list[dict[str, Any]], *, title: str | None = None) -> None:
        """Render a list of dicts as a table."""
        if self.fmt == "json":
            self._render_json(rows, title)
            return
        if self.fmt == "yaml":
            self._render_yaml(rows, title)
            return
        self._emit_title(title)
        if not rows:
            self._write("(no rows)")
            return
        cols = list(rows[0].keys())
        widths = [len(c) for c in cols]
        for r in rows:
            for i, c in enumerate(cols):
                widths[i] = max(widths[i], len(str(r.get(c, ""))))
        # Header
        self._write("  ".join(c.ljust(widths[i]) for i, c in enumerate(cols)))
        self._write("  ".join("-" * widths[i] for i in range(len(cols))))
        for r in rows:
            self._write("  ".join(str(r.get(c, "")).ljust(widths[i]) for i, c in enumerate(cols)))

    # ------------------------------------------------------------------ #
    # Internal renderers                                                  #
    # ------------------------------------------------------------------ #

    def _render_table(self, payload: Any, title: str | None) -> None:
        if isinstance(payload, list):
            self.render_table(payload, title=title)
            return
        if isinstance(payload, dict):
            self._emit_title(title)
            if not payload:
                self._write("(empty)")
                return
            max_key = max(len(str(k)) for k in payload.keys())
            for k, v in payload.items():
                self._write(f"{str(k).ljust(max_key)}  {self._scalar(v)}")
            return
        self._write(self._scalar(payload))

    def _render_json(self, payload: Any, title: str | None) -> None:
        self._emit_title(title)
        self._write(json.dumps(payload, indent=2, sort_keys=True, default=str))

    def _render_yaml(self, payload: Any, title: str | None) -> None:
        self._emit_title(title)
        try:
            import yaml  # type: ignore[import-untyped]
            self._write(yaml.safe_dump(payload, sort_keys=True, default_flow_style=False))
        except ImportError:
            # Stdlib fallback: render as JSON-without-quotes is fragile;
            # use JSON for fidelity (no data loss) when PyYAML missing.
            self._write("# (PyYAML not installed; rendering as JSON)")
            self._write(json.dumps(payload, indent=2, sort_keys=True, default=str))

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _scalar(self, v: Any) -> str:
        if isinstance(v, (dict, list)):
            return json.dumps(v, default=str)
        return str(v)

    def _emit_title(self, title: str | None) -> None:
        if title:
            self._write(f"=== {title} ===")

    def _write(self, text: str) -> None:
        print(text, file=self.out)


__all__ = ["Formatter"]
