"""ToolRegistry — registers, unregisters, looks up, and lists tools.

Validates the `ToolContract` on register; tracks per-tool availability
state (available / disabled / errored) so callers can short-circuit
lookups without invoking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from albra_seed.core.base import Tool, ToolContract
from albra_seed.tools.contract_validator import ContractValidator


# =====================================================================
# Availability state
# =====================================================================


class ToolAvailability(str, Enum):
    AVAILABLE = "available"
    DISABLED = "disabled"
    ERRORED = "errored"
    NOT_CONFIGURED = "not_configured"


@dataclass
class ToolRecord:
    """Internal registry record."""

    tool: Tool
    contract: ToolContract
    availability: ToolAvailability = ToolAvailability.AVAILABLE
    registered_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolAlreadyRegisteredError(KeyError):
    pass


class ToolNotFoundError(KeyError):
    pass


class InvalidToolContractError(ValueError):
    pass


# =====================================================================
# ToolRegistry
# =====================================================================


class ToolRegistry:
    """Universal tool registry. Validates contracts at registration time and
    maintains per-tool availability state for fast lookup."""

    def __init__(self, *, validator: ContractValidator | None = None) -> None:
        self._tools: dict[str, ToolRecord] = {}
        self._validator = validator or ContractValidator()

    # ------------------------------------------------------------------
    # Register / unregister
    # ------------------------------------------------------------------
    def register(
        self,
        tool: Tool,
        *,
        metadata: dict[str, Any] | None = None,
        availability: ToolAvailability = ToolAvailability.AVAILABLE,
    ) -> ToolContract:
        contract = self._validate_contract(tool)
        if contract.name in self._tools:
            raise ToolAlreadyRegisteredError(contract.name)
        from albra_seed.core.types import utc_now

        rec = ToolRecord(
            tool=tool,
            contract=contract,
            availability=availability,
            registered_at=utc_now().isoformat(),
            metadata=dict(metadata or {}),
        )
        self._tools[contract.name] = rec
        return contract

    def unregister(self, name: str) -> bool:
        rec = self._tools.pop(name, None)
        return rec is not None

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get(self, name: str) -> Tool:
        rec = self._require(name)
        if rec.availability != ToolAvailability.AVAILABLE:
            raise ToolNotFoundError(f"tool '{name}' is {rec.availability.value}")
        return rec.tool

    def get_contract(self, name: str) -> ToolContract:
        return self._require(name).contract

    def list(self) -> list[str]:
        return list(self._tools.keys())

    def list_available(self) -> list[str]:
        return [n for n, r in self._tools.items() if r.availability == ToolAvailability.AVAILABLE]

    def availability(self, name: str) -> ToolAvailability:
        rec = self._tools.get(name)
        if rec is None:
            return ToolAvailability.NOT_CONFIGURED
        return rec.availability

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------
    def set_availability(self, name: str, availability: ToolAvailability) -> None:
        rec = self._require(name)
        rec.availability = availability

    def enable(self, name: str) -> None:
        self.set_availability(name, ToolAvailability.AVAILABLE)

    def disable(self, name: str) -> None:
        self.set_availability(name, ToolAvailability.DISABLED)

    def mark_errored(self, name: str) -> None:
        self.set_availability(name, ToolAvailability.ERRORED)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------
    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "name": n,
                "version": r.contract.version,
                "risk_level": r.contract.risk_level.value,
                "availability": r.availability.value,
                "registered_at": r.registered_at,
                "is_dry_run_capable": r.contract.is_dry_run_capable,
            }
            for n, r in self._tools.items()
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _require(self, name: str) -> ToolRecord:
        rec = self._tools.get(name)
        if rec is None:
            raise ToolNotFoundError(name)
        return rec

    def _validate_contract(self, tool: Tool) -> ToolContract:
        contract = getattr(tool, "contract", None)
        if contract is None or not isinstance(contract, ToolContract):
            raise InvalidToolContractError(f"tool {type(tool).__name__} has no ToolContract")
        if not contract.name:
            raise InvalidToolContractError("ToolContract.name must be set")
        # Sanity check: input_schema and output_schema (if present) must be dicts
        if not isinstance(contract.input_schema, dict):
            raise InvalidToolContractError("input_schema must be a dict")
        if not isinstance(contract.output_schema, dict):
            raise InvalidToolContractError("output_schema must be a dict")
        return contract
