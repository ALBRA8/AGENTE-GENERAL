"""Voice adapter — lazy-imports an external ASR engine (e.g. `whisper`).

If no ASR engine is available, the adapter reports `is_ready=False` and
raises `ModalityNotConfiguredError` on encode/decode.
"""

from __future__ import annotations

import importlib
from typing import Any

from albra_seed.core.types import ModalityType
from albra_seed.multimodal.base import (
    ModalityAdapter,
    ModalityNotConfiguredError,
    ModalityPayload,
    ModalityValidationError,
)

# Module path of the optional ASR engine. Overridable via __init__.
_DEFAULT_ASR_MODULE = "whisper"


class VoiceAdapter(ModalityAdapter):
    """Encodes/decodes voice data. Always importable; only the lazy
    import of `whisper` (or equivalent) is required at call time."""

    modality = ModalityType.VOICE
    name = "voice"

    def __init__(
        self,
        *,
        asr_module: str = _DEFAULT_ASR_MODULE,
        sample_rate: int = 16000,
    ) -> None:
        super().__init__()
        self._asr_module = asr_module
        self._sample_rate = sample_rate
        self._asr: Any = None

    def _probe_ready(self) -> bool:
        try:
            mod = importlib.import_module(self._asr_module)
        except ImportError:
            return False
        # Some ASR libs expose `load_model` / `transcribe`. Treat either as
        # sufficient evidence of readiness.
        return hasattr(mod, "transcribe") or hasattr(mod, "load_model") or hasattr(mod, "load")

    def _load_asr(self) -> Any:
        if self._asr is not None:
            return self._asr
        self.require_ready()
        try:
            mod = importlib.import_module(self._asr_module)
        except ImportError as exc:
            raise ModalityNotConfiguredError(
                f"ASR module '{self._asr_module}' is not installed"
            ) from exc
        self._asr = mod
        return self._asr

    def validate(self, content: Any) -> bool:
        if content is None:
            return False
        if isinstance(content, (bytes, bytearray)):
            return len(content) > 0
        if isinstance(content, str):
            # plain text — treated as a transcript source
            return True
        return False

    async def encode(self, content: Any) -> ModalityPayload:
        # Encode normalises input into a binary payload. If content is text,
        # we treat it as a transcript (mime_type=text/plain) so the consumer
        # knows no audio bytes are attached.
        if not self.validate(content):
            raise ModalityValidationError(
                f"voice adapter cannot encode {type(content).__name__}"
            )
        if isinstance(content, str):
            return ModalityPayload(
                modality=self.modality,
                mime_type="text/plain",
                encoding="utf-8",
                data=content,
                metadata={"transcript": True, "sample_rate": self._sample_rate},
            )
        return ModalityPayload(
            modality=self.modality,
            mime_type="audio/wav",
            encoding="binary",
            data=bytes(content),
            metadata={"sample_rate": self._sample_rate, "length_bytes": len(content)},
        )

    async def decode(self, payload: ModalityPayload) -> str:
        """Transcribes audio bytes via the configured ASR engine. Raises
        if the ASR module is not installed."""
        if payload.modality != self.modality:
            raise ModalityValidationError(
                f"payload modality mismatch: {payload.modality} != {self.modality}"
            )
        # If the payload is already a transcript, return it directly.
        if payload.metadata.get("transcript") and isinstance(payload.data, str):
            return payload.data
        # Otherwise, we need the ASR engine to transcribe audio bytes.
        self.require_ready()
        asr = self._load_asr()
        if not isinstance(payload.data, (bytes, bytearray)):
            raise ModalityValidationError(
                "voice payload data must be bytes for transcription"
            )
        # Minimal generic call — different ASR libs have different APIs;
        # we rely on the convention `transcribe(audio_bytes) -> str`.
        transcribe = getattr(asr, "transcribe", None)
        if transcribe is None:
            raise ModalityNotConfiguredError(
                f"ASR module '{self._asr_module}' has no `transcribe` function"
            )
        return str(transcribe(bytes(payload.data)))


__all__ = ["VoiceAdapter"]
