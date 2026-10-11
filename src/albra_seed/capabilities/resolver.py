"""CapabilityResolver — verifies prerequisites of a capability are satisfied.

A `Capability` requires tools, providers, permissions. The resolver
checks each against the available sets and returns a structured
`Resolution` with `satisfied` + `missing`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from albra_seed.capabilities.registry import CapabilityNotFoundError, CapabilityRegistry


# =====================================================================
# Resolution result
# =====================================================================


@dataclass
class Resolution:
    """Result of resolving a capability's prerequisites."""

    capability: str
    satisfied: bool
    missing_tools: list[str] = field(default_factory=list)
    missing_providers: list[str] = field(default_factory=list)
    missing_permissions: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "satisfied": self.satisfied,
            "missing": {
                "tools": list(self.missing_tools),
                "providers": list(self.missing_providers),
                "permissions": list(self.missing_permissions),
            },
            "reasons": list(self.reasons),
        }


# =====================================================================
# CapabilityResolver
# =====================================================================


class CapabilityResolver:
    """Given a desired capability + available tool/provider/permission sets,
    determine whether the capability can be exercised."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        available_tools: set[str] | None = None,
        available_providers: set[str] | None = None,
        available_permissions: set[str] | None = None,
    ) -> None:
        self.registry = registry
        self.available_tools = set(available_tools or [])
        self.available_providers = set(available_providers or [])
        self.available_permissions = set(available_permissions or [])

    # ------------------------------------------------------------------
    # Update available sets
    # ------------------------------------------------------------------
    def set_available_tools(self, tools: set[str]) -> None:
        self.available_tools = set(tools)

    def set_available_providers(self, providers: set[str]) -> None:
        self.available_providers = set(providers)

    def set_available_permissions(self, permissions: set[str]) -> None:
        self.available_permissions = set(permissions)

    # ------------------------------------------------------------------
    def resolve(self, name: str) -> Resolution:
        try:
            cap = self.registry.get(name)
        except CapabilityNotFoundError:
            return Resolution(
                capability=name,
                satisfied=False,
                reasons=[f"capability '{name}' not registered"],
            )

        if not self.registry.is_enabled(name):
            return Resolution(
                capability=name,
                satisfied=False,
                reasons=[f"capability '{name}' is disabled"],
            )

        missing_tools = [t for t in cap.requires_tools if t not in self.available_tools]
        missing_providers = [p for p in cap.requires_providers if p not in self.available_providers]
        missing_permissions = [p for p in cap.requires_permissions if p not in self.available_permissions]

        satisfied = not (missing_tools or missing_providers or missing_permissions)
        reasons: list[str] = []
        if missing_tools:
            reasons.append(f"missing tools: {missing_tools}")
        if missing_providers:
            reasons.append(f"missing providers: {missing_providers}")
        if missing_permissions:
            reasons.append(f"missing permissions: {missing_permissions}")
        return Resolution(
            capability=name,
            satisfied=satisfied,
            missing_tools=missing_tools,
            missing_providers=missing_providers,
            missing_permissions=missing_permissions,
            reasons=reasons,
        )

    def resolve_all(self) -> list[Resolution]:
        return [self.resolve(n) for n in self.registry.list()]
