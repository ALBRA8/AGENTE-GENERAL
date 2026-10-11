"""CLI context — shared state for every command.

Holds the Runtime + ConfigManager + Formatter. Built lazily so
`albra --help` doesn't trigger a full runtime startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from albra_seed.config import ConfigManager
from albra_seed.runtime.runtime import Runtime


@dataclass
class CLIContext:
    """Shared state for CLI command handlers."""

    runtime: Runtime
    config: ConfigManager
    formatter_name: str = "table"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        runtime: Runtime | None = None,
        config: ConfigManager | None = None,
        formatter: str = "table",
    ) -> "CLIContext":
        from albra_seed.runtime.runtime import Runtime as _RT
        rt = runtime or _RT()
        cfg = config or ConfigManager(seed=rt.config)
        # If a fresh ConfigManager was constructed, load env vars on top
        # of the runtime's existing SeedConfig so the CLI reflects the
        # process environment. Pre-built ConfigManagers are left alone
        # (the caller owns their lifecycle).
        if config is None:
            try:
                cfg.load()
            except Exception:
                # Failing to load env must not crash the CLI.
                pass
        return cls(runtime=rt, config=cfg, formatter_name=formatter)

    @property
    def default_agent(self) -> Any | None:
        # No global agent registry in CORE_ONLY mode; return None unless
        # the caller registered one in `extra["agents"]`.
        agents = self.extra.get("agents") or {}
        if not agents:
            return None
        return next(iter(agents.values()))


def make_default_context(formatter: str = "table") -> CLIContext:
    """Build a CLIContext from defaults (CORE_ONLY friendly)."""
    return CLIContext.build(formatter=formatter)


__all__ = ["CLIContext", "make_default_context"]
