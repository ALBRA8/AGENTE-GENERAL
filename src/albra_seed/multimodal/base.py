"""Base modality adapter — abstract encode/decode/validate interface.

A `ModalityAdapter` converts between native Python content (str, bytes,
PIL.Image, etc.) and a canonical transport payload. The SEED ships a
working `TextAdapter`; voice/image/document adapters raise if their
optional dependency is not installed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import ModalityType, utc_now


class ModalityPayload(BaseModel):
    """Canonical envelope for a modality payload."""

    modality: ModalityType
    mime_type: str = "application/octet-stream"
    encoding: str = "utf-8"  # for text; ignored for binary
    data: Any = None  # str (text) or bytes (binary)
    metadata: dict[str, Any] = Field(default_factory=dict)
    encoded_at: Any = Field(default_factory=utc_now)


class ModalityAdapter(ABC):
    """Abstract modality adapter.

    Concrete adapters MUST be importable even when their backing library
    is not installed; the library is only imported lazily inside
    `__init__` (or at the first call), and `is_ready` returns False when
    it is missing.
    """

    modality: ModalityType
    name: str

    def __init__(self) -> None:
        self._ready: bool | None = None  # cached readiness

    @abstractmethod
    async def encode(self, content: Any) -> ModalityPayload:
        raise NotImplementedError

    @abstractmethod
    async def decode(self, payload: ModalityPayload) -> Any:
        raise NotImplementedError

    @abstractmethod
    def validate(self, content: Any) -> bool:
        raise NotImplementedError

    @property
    def is_ready(self) -> bool:
        """Lazy-cached readiness probe. Subclasses override `_probe_ready`."""
        if self._ready is None:
            self._ready = self._probe_ready()
        return self._ready

    def _probe_ready(self) -> bool:
        """Override in subclasses to return True only when the optional
        dependency is importable. Default: True (no deps required)."""
        return True

    def require_ready(self) -> None:
        if not self.is_ready:
            raise ModalityNotConfiguredError(
                f"modality adapter '{self.name}' is not configured "
                f"(optional dependency missing or unset)"
            )


class ModalityNotConfiguredError(RuntimeError):
    """Raised when a modality adapter is used without its backing dependency."""


class ModalityValidationError(ValueError):
    """Raised when input content fails validation before encode/decode."""


__all__ = [
    "ModalityAdapter",
    "ModalityPayload",
    "ModalityNotConfiguredError",
    "ModalityValidationError",
]
