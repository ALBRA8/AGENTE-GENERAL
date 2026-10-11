"""Text adapter — default modality, always available (no external deps)."""

from __future__ import annotations

from typing import Any

from albra_seed.core.types import ModalityType
from albra_seed.multimodal.base import (
    ModalityAdapter,
    ModalityPayload,
    ModalityValidationError,
)


class TextAdapter(ModalityAdapter):
    """Encodes/decodes plain UTF-8 text. Always available."""

    modality = ModalityType.TEXT
    name = "text"

    def validate(self, content: Any) -> bool:
        if content is None:
            return False
        if isinstance(content, str):
            return True
        if isinstance(content, (bytes, bytearray)):
            try:
                content.decode("utf-8")
                return True
            except UnicodeDecodeError:
                return False
        return False

    async def encode(self, content: Any) -> ModalityPayload:
        if not self.validate(content):
            raise ModalityValidationError(
                f"text adapter cannot encode {type(content).__name__}"
            )
        if isinstance(content, (bytes, bytearray)):
            text = bytes(content).decode("utf-8")
        else:
            text = str(content)
        return ModalityPayload(
            modality=self.modality,
            mime_type="text/plain",
            encoding="utf-8",
            data=text,
            metadata={"length": len(text)},
        )

    async def decode(self, payload: ModalityPayload) -> str:
        if payload.modality != self.modality:
            raise ModalityValidationError(
                f"payload modality mismatch: {payload.modality} != {self.modality}"
            )
        if not isinstance(payload.data, str):
            raise ModalityValidationError(
                "text payload data must be a string"
            )
        return payload.data


__all__ = ["TextAdapter"]
