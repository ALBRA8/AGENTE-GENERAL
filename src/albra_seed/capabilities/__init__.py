"""Capabilities subpackage.

A `Capability` describes WHAT an agent can technically do (declarative).
It is distinct from:
  - `Skill` (HOW to do it)
  - `Knowledge` (what it knows)
  - `Memory` (what it remembers)
  - `Permission` (what it's authorized to do)
"""

from albra_seed.capabilities.matrix import CapabilityMatrix, MatrixEntry
from albra_seed.capabilities.registry import (
    CapabilityAlreadyRegisteredError,
    CapabilityNotFoundError,
    CapabilityRecord,
    CapabilityRegistry,
)
from albra_seed.capabilities.resolver import CapabilityResolver, Resolution

__all__ = [
    "CapabilityMatrix",
    "MatrixEntry",
    "CapabilityAlreadyRegisteredError",
    "CapabilityNotFoundError",
    "CapabilityRecord",
    "CapabilityRegistry",
    "CapabilityResolver",
    "Resolution",
]
