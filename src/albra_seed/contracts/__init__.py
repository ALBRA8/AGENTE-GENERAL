"""Contracts subpackage — structured output contracts.

Public API:
    from albra_seed.contracts import Contract, ContractRegistry
"""

from __future__ import annotations

from albra_seed.contracts.base import Contract
from albra_seed.contracts.registry import ContractRegistry

__all__ = ["Contract", "ContractRegistry"]
