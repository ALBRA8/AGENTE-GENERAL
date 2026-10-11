"""CapabilityRegistry — registers capabilities and tracks their state.

A `Capability` describes WHAT an agent can technically do (not how —
that's a `Skill`; not what it knows — that's Knowledge). It declares
the tools, providers, and permissions it requires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from albra_seed.core.base import Capability


# =====================================================================
# Errors
# =====================================================================


class CapabilityAlreadyRegisteredError(KeyError):
    pass


class CapabilityNotFoundError(KeyError):
    pass


# =====================================================================
# Registry record
# =====================================================================


@dataclass
class CapabilityRecord:
    capability: Capability
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


# =====================================================================
# CapabilityRegistry
# =====================================================================


class CapabilityRegistry:
    """Tracks capabilities and their enable/disable state. Does NOT execute —
    that's the SkillExecutor's job. Use CapabilityResolver to verify a
    capability's prerequisites are met."""

    def __init__(self) -> None:
        self._caps: dict[str, CapabilityRecord] = {}

    # ------------------------------------------------------------------
    def register(
        self,
        capability: Capability,
        *,
        enabled: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> Capability:
        if capability.name in self._caps:
            raise CapabilityAlreadyRegisteredError(capability.name)
        self._caps[capability.name] = CapabilityRecord(
            capability=capability,
            enabled=enabled,
            metadata=dict(metadata or {}),
        )
        return capability

    def unregister(self, name: str) -> bool:
        return self._caps.pop(name, None) is not None

    # ------------------------------------------------------------------
    def get(self, name: str) -> Capability:
        rec = self._require(name)
        return rec.capability

    def list(self) -> list[str]:
        return list(self._caps.keys())

    def list_enabled(self) -> list[str]:
        return [n for n, r in self._caps.items() if r.enabled]

    def is_enabled(self, name: str) -> bool:
        rec = self._caps.get(name)
        return bool(rec and rec.enabled)

    # ------------------------------------------------------------------
    def enable(self, name: str) -> None:
        self._require(name).enabled = True

    def disable(self, name: str) -> None:
        self._require(name).enabled = False

    # ------------------------------------------------------------------
    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "name": n,
                "version": r.capability.version,
                "enabled": r.enabled,
                "requires_tools": list(r.capability.requires_tools),
                "requires_providers": list(r.capability.requires_providers),
                "requires_permissions": list(r.capability.requires_permissions),
                "risk_level": r.capability.risk_level.value,
                "is_external": r.capability.is_external,
            }
            for n, r in self._caps.items()
        ]

    # ------------------------------------------------------------------
    def _require(self, name: str) -> CapabilityRecord:
        rec = self._caps.get(name)
        if rec is None:
            raise CapabilityNotFoundError(name)
        return rec
