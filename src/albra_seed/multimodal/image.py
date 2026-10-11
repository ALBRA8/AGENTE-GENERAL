"""Image adapter — lazy-imports PIL (Pillow) for image encoding/decoding."""

from __future__ import annotations

import importlib
import io
from typing import Any

from albra_seed.core.types import ModalityType
from albra_seed.multimodal.base import (
    ModalityAdapter,
    ModalityNotConfiguredError,
    ModalityPayload,
    ModalityValidationError,
)

_DEFAULT_PIL_MODULE = "PIL.Image"


class ImageAdapter(ModalityAdapter):
    """Encodes/decodes image content. PIL is lazy-imported; if it is not
    installed, `is_ready` returns False and encode/decode raise."""

    modality = ModalityType.IMAGE
    name = "image"

    def __init__(
        self,
        *,
        pil_module: str = _DEFAULT_PIL_MODULE,
        default_format: str = "PNG",
    ) -> None:
        super().__init__()
        self._pil_module = pil_module
        self._default_format = default_format

    def _probe_ready(self) -> bool:
        try:
            importlib.import_module(self._pil_module)
        except ImportError:
            return False
        return True

    def _load_pil(self) -> Any:
        self.require_ready()
        try:
            return importlib.import_module(self._pil_module)
        except ImportError as exc:
            raise ModalityNotConfiguredError(
                "Pillow is not installed; install `pillow` to use ImageAdapter"
            ) from exc

    def validate(self, content: Any) -> bool:
        if content is None:
            return False
        if isinstance(content, (bytes, bytearray)):
            return len(content) > 0
        if isinstance(content, str):
            return True  # treat as path or URL — actual load is lazy
        # PIL.Image instance — validate via duck-typing
        return hasattr(content, "save") and hasattr(content, "tobytes")

    async def encode(self, content: Any) -> ModalityPayload:
        if not self.validate(content):
            raise ModalityValidationError(
                f"image adapter cannot encode {type(content).__name__}"
            )
        # If content is already bytes, treat as raw image data.
        if isinstance(content, (bytes, bytearray)):
            return ModalityPayload(
                modality=self.modality,
                mime_type="application/octet-stream",
                encoding="binary",
                data=bytes(content),
                metadata={"length_bytes": len(content)},
            )
        # If content is a PIL Image (or duck-typed), serialise via save().
        if hasattr(content, "save") and hasattr(content, "tobytes"):
            buf = io.BytesIO()
            try:
                content.save(buf, format=self._default_format)
            except Exception as exc:  # pragma: no cover - defensive
                raise ModalityValidationError(f"image encode failed: {exc}") from exc
            data = buf.getvalue()
            return ModalityPayload(
                modality=self.modality,
                mime_type=f"image/{self._default_format.lower()}",
                encoding="binary",
                data=data,
                metadata={"format": self._default_format, "length_bytes": len(data)},
            )
        # Otherwise content is a string path/URL — store the reference only.
        return ModalityPayload(
            modality=self.modality,
            mime_type="text/uri-list",
            encoding="utf-8",
            data=str(content),
            metadata={"ref": True},
        )

    async def decode(self, payload: ModalityPayload) -> Any:
        if payload.modality != self.modality:
            raise ModalityValidationError(
                f"payload modality mismatch: {payload.modality} != {self.modality}"
            )
        if payload.metadata.get("ref") and isinstance(payload.data, str):
            return payload.data  # caller resolves path/URL externally
        if not isinstance(payload.data, (bytes, bytearray)):
            raise ModalityValidationError(
                "image payload data must be bytes"
            )
        # Lazy-load PIL only when actually decoding image bytes.
        Image = self._load_pil()
        try:
            return Image.open(io.BytesIO(bytes(payload.data)))
        except Exception as exc:  # pragma: no cover - defensive
            raise ModalityValidationError(f"image decode failed: {exc}") from exc


__all__ = ["ImageAdapter"]
