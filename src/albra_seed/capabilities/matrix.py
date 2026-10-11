"""CapabilityMatrix — maps capability → requirements + risk.

Used by the PolicyEngine to look up what each capability requires
(tools/providers/permissions) and its risk level, so policy decisions
can be made without re-loading capability objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from albra_seed.capabilities.registry import CapabilityRegistry
from albra_seed.core.types import RiskLevel


# =====================================================================
# Matrix entry
# =====================================================================


@dataclass
class MatrixEntry:
    capability: str
    requires_tools: list[str]
    requires_providers: list[str]
    requires_permissions: list[str]
    risk_level: RiskLevel
    is_external: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "requires_tools": list(self.requires_tools),
            "requires_providers": list(self.requires_providers),
            "requires_permissions": list(self.requires_permissions),
            "risk_level": self.risk_level.value,
            "is_external": self.is_external,
            "extra": dict(self.extra),
        }


# =====================================================================
# CapabilityMatrix
# =====================================================================


class CapabilityMatrix:
    """Static lookup table built from the registry. PolicyEngine uses this
    to determine required autonomy/permissions without traversing capability
    objects on every policy evaluation."""

    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self._entries: dict[str, MatrixEntry] = {}
        if registry is not None:
            self.load_from_registry(registry)

    # ------------------------------------------------------------------
    def load_from_registry(self, registry: CapabilityRegistry) -> None:
        for snap in registry.snapshot():
            self._entries[snap["name"]] = MatrixEntry(
                capability=snap["name"],
                requires_tools=list(snap["requires_tools"]),
                requires_providers=list(snap["requires_providers"]),
                requires_permissions=list(snap["requires_permissions"]),
                risk_level=RiskLevel(snap["risk_level"]),
                is_external=snap["is_external"],
            )

    # ------------------------------------------------------------------
    def add(
        self,
        capability: str,
        *,
        requires_tools: list[str] | None = None,
        requires_providers: list[str] | None = None,
        requires_permissions: list[str] | None = None,
        risk_level: RiskLevel = RiskLevel.LOW,
        is_external: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> MatrixEntry:
        entry = MatrixEntry(
            capability=capability,
            requires_tools=list(requires_tools or []),
            requires_providers=list(requires_providers or []),
            requires_permissions=list(requires_permissions or []),
            risk_level=risk_level,
            is_external=is_external,
            extra=dict(extra or {}),
        )
        self._entries[capability] = entry
        return entry

    # ------------------------------------------------------------------
    def get(self, capability: str) -> MatrixEntry | None:
        return self._entries.get(capability)

    def requires_tools(self, capability: str) -> list[str]:
        e = self._entries.get(capability)
        return list(e.requires_tools) if e else []

    def requires_providers(self, capability: str) -> list[str]:
        e = self._entries.get(capability)
        return list(e.requires_providers) if e else []

    def requires_permissions(self, capability: str) -> list[str]:
        e = self._entries.get(capability)
        return list(e.requires_permissions) if e else []

    def risk_level(self, capability: str) -> RiskLevel:
        e = self._entries.get(capability)
        return e.risk_level if e else RiskLevel.UNKNOWN

    def is_external(self, capability: str) -> bool:
        e = self._entries.get(capability)
        return e.is_external if e else False

    def all(self) -> list[MatrixEntry]:
        return list(self._entries.values())

    def snapshot(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries.values()]
