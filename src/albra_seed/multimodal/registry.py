"""Multimodal registry — register + retrieve modality adapters.

The registry also exposes `is_ready(modality)` so callers can probe
whether a given modality is usable without instantiating it.
"""

from __future__ import annotations

from albra_seed.core.types import ModalityType
from albra_seed.multimodal.base import ModalityAdapter
from albra_seed.multimodal.document import DocumentAdapter
from albra_seed.multimodal.image import ImageAdapter
from albra_seed.multimodal.text import TextAdapter
from albra_seed.multimodal.voice import VoiceAdapter


class MultimodalRegistry:
    """Registry of modality adapters.

    The TEXT adapter is always registered. VOICE / IMAGE / DOCUMENT
    adapters are registered too, but their `is_ready` reflects whether
    their optional dependency is importable.
    """

    def __init__(
        self,
        *,
        text: TextAdapter | None = None,
        voice: VoiceAdapter | None = None,
        image: ImageAdapter | None = None,
        document: DocumentAdapter | None = None,
    ) -> None:
        self._adapters: dict[ModalityType, ModalityAdapter] = {}
        self.register(text or TextAdapter())
        self.register(voice or VoiceAdapter())
        self.register(image or ImageAdapter())
        self.register(document or DocumentAdapter())

    def register(self, adapter: ModalityAdapter) -> None:
        if not isinstance(adapter, ModalityAdapter):
            raise TypeError("adapter must be a ModalityAdapter")
        self._adapters[adapter.modality] = adapter

    def unregister(self, modality: ModalityType) -> bool:
        if modality == ModalityType.TEXT:
            # The TEXT adapter must always be present.
            return False
        return self._adapters.pop(modality, None) is not None

    def get(self, modality: ModalityType) -> ModalityAdapter | None:
        return self._adapters.get(modality)

    def require(self, modality: ModalityType) -> ModalityAdapter:
        adapter = self._adapters.get(modality)
        if adapter is None:
            raise KeyError(f"no adapter registered for modality: {modality}")
        return adapter

    def list_modalities(self) -> list[ModalityType]:
        return list(self._adapters.keys())

    def is_ready(self, modality: ModalityType) -> bool:
        adapter = self._adapters.get(modality)
        if adapter is None:
            return False
        return adapter.is_ready

    def ready_modalities(self) -> list[ModalityType]:
        return [m for m in self._adapters if self._adapters[m].is_ready]

    def __len__(self) -> int:
        return len(self._adapters)


__all__ = ["MultimodalRegistry"]
