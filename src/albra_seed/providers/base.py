"""Re-export the LLMProvider interface from core.base.

The Provider layer does NOT redefine `LLMProvider`. The canonical
contract lives in `albra_seed.core.base` so the rest of the SEED
(runtime, tools, reasoning, …) can depend on it without importing the
provider subpackage. This module simply re-exports it for convenience
so callers can `from albra_seed.providers.base import LLMProvider`
when working purely inside the provider layer.
"""

from __future__ import annotations

from albra_seed.core.base import LLMProvider

__all__ = ["LLMProvider"]
