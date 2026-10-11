"""Multimodal subpackage — TEXT/VOICE/IMAGE/DOCUMENT adapters.

The TEXT adapter is always available. The voice/image/document adapters
are always importable but raise `ModalityNotConfiguredError` if their
optional dependency is not installed (`whisper` / `pillow` / `pypdf` /
`python-docx`). The `MultimodalRegistry` exposes `is_ready(modality)`
so callers can probe readiness before invoking encode/decode.
"""

from albra_seed.multimodal.base import (
    ModalityAdapter,
    ModalityNotConfiguredError,
    ModalityPayload,
    ModalityValidationError,
)
from albra_seed.multimodal.document import DocumentAdapter
from albra_seed.multimodal.image import ImageAdapter
from albra_seed.multimodal.registry import MultimodalRegistry
from albra_seed.multimodal.text import TextAdapter
from albra_seed.multimodal.voice import VoiceAdapter

__all__ = [
    # base
    "ModalityAdapter",
    "ModalityPayload",
    "ModalityNotConfiguredError",
    "ModalityValidationError",
    # adapters
    "TextAdapter",
    "VoiceAdapter",
    "ImageAdapter",
    "DocumentAdapter",
    # registry
    "MultimodalRegistry",
]
