"""Contract registry — stores and validates structured output contracts.

The SEED itself ships NO domain contracts. Specialization layers
register contracts here; consumers can then look them up by name and
validate payloads against them.
"""

from __future__ import annotations

from typing import Any

import structlog

from albra_seed.contracts.base import Contract

log = structlog.get_logger("albra.contracts.registry")


class ContractRegistry:
    """In-memory registry of structured-output contracts."""

    def __init__(self) -> None:
        self._contracts: dict[str, Contract] = {}

    # ------------------------------------------------------------------ #
    # Mutators                                                          #
    # ------------------------------------------------------------------ #

    def register(self, contract: Contract, *, override: bool = False) -> None:
        """Register a contract. Name must be unique unless override=True."""
        name = contract.name
        if name in self._contracts and not override:
            raise ValueError(
                f"contract '{name}' already registered; pass override=True to replace"
            )
        self._contracts[name] = contract
        log.info("contract.registered", name=name, version=contract.version)

    def unregister(self, name: str) -> bool:
        return self._contracts.pop(name, None) is not None

    # ------------------------------------------------------------------ #
    # Lookups                                                            #
    # ------------------------------------------------------------------ #

    def get(self, name: str) -> Contract | None:
        return self._contracts.get(name)

    def require(self, name: str) -> Contract:
        c = self.get(name)
        if c is None:
            raise KeyError(f"contract not registered: {name}")
        return c

    def list_names(self) -> list[str]:
        return sorted(self._contracts.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._contracts

    def __len__(self) -> int:
        return len(self._contracts)

    # ------------------------------------------------------------------ #
    # Validation                                                          #
    # ------------------------------------------------------------------ #

    def validate_input(self, name: str, data: Any) -> list[str]:
        """Validate an input payload against a registered contract."""
        c = self.get(name)
        if c is None:
            return [f"contract not registered: {name}"]
        return c.validate_input(data)

    def validate_output(self, name: str, data: Any) -> list[str]:
        """Validate an output payload against a registered contract."""
        c = self.get(name)
        if c is None:
            return [f"contract not registered: {name}"]
        return c.validate_output(data)

    def assert_input(self, name: str, data: Any) -> None:
        """Raise ValueError if the input is invalid."""
        errors = self.validate_input(name, data)
        if errors:
            raise ValueError(f"contract '{name}' input invalid: {errors}")

    def assert_output(self, name: str, data: Any) -> None:
        """Raise ValueError if the output is invalid."""
        errors = self.validate_output(name, data)
        if errors:
            raise ValueError(f"contract '{name}' output invalid: {errors}")

    # ------------------------------------------------------------------ #
    # Snapshot                                                          #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": len(self._contracts),
            "names": self.list_names(),
            "contracts": {n: c.snapshot() for n, c in self._contracts.items()},
        }


__all__ = ["ContractRegistry"]
