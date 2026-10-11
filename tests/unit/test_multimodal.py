"""Unit tests for the multimodal subpackage."""

import asyncio

import pytest

from albra_seed.core.types import ModalityType
from albra_seed.multimodal import (
    DocumentAdapter,
    ImageAdapter,
    ModalityNotConfiguredError,
    ModalityPayload,
    ModalityValidationError,
    MultimodalRegistry,
    TextAdapter,
    VoiceAdapter,
)


# -------------------------------------------------------------------
# 1. Text adapter is default + always ready
# -------------------------------------------------------------------


def test_text_adapter_default():
    adapter = TextAdapter()
    assert adapter.modality == ModalityType.TEXT
    assert adapter.is_ready is True

    payload = asyncio.run(adapter.encode("hello world"))
    assert isinstance(payload, ModalityPayload)
    assert payload.modality == ModalityType.TEXT
    assert payload.mime_type == "text/plain"
    assert payload.data == "hello world"

    decoded = asyncio.run(adapter.decode(payload))
    assert decoded == "hello world"

    # Bytes encoded as UTF-8 are accepted too.
    payload_bytes = asyncio.run(adapter.encode(b"raw bytes"))
    assert payload_bytes.data == "raw bytes"

    # Invalid content raises.
    with pytest.raises(ModalityValidationError):
        asyncio.run(adapter.encode(123))


# -------------------------------------------------------------------
# 2. Voice adapter raises if not configured
# -------------------------------------------------------------------


def test_voice_adapter_raises_if_not_configured():
    adapter = VoiceAdapter(asr_module="definitely_not_installed_asr_module_xyz")
    assert adapter.is_ready is False

    # Encode a transcript string still works (no ASR needed for text).
    payload = asyncio.run(adapter.encode("hello transcript"))
    assert payload.metadata.get("transcript") is True

    # Decode of audio bytes requires the ASR module -> raises.
    audio_payload = ModalityPayload(
        modality=ModalityType.VOICE,
        mime_type="audio/wav",
        encoding="binary",
        data=b"\x00\x01\x02\x03",
    )
    with pytest.raises(ModalityNotConfiguredError):
        asyncio.run(adapter.decode(audio_payload))


# -------------------------------------------------------------------
# 3. Multimodal registry is_ready reflects optional deps
# -------------------------------------------------------------------


def test_multimodal_registry_is_ready():
    registry = MultimodalRegistry()
    # TEXT always ready.
    assert registry.is_ready(ModalityType.TEXT) is True
    # VOICE / IMAGE / DOCUMENT readiness depends on whether the optional
    # dependency is importable in this environment. We do not assume any
    # specific state, just that the method runs without raising.
    for mod in [
        ModalityType.VOICE,
        ModalityType.IMAGE,
        ModalityType.DOCUMENT,
    ]:
        ready = registry.is_ready(mod)
        assert isinstance(ready, bool)

    # Ready modalities always include TEXT.
    ready_set = set(registry.ready_modalities())
    assert ModalityType.TEXT in ready_set

    # get / require / unregister semantics.
    assert registry.get(ModalityType.TEXT) is not None
    assert registry.require(ModalityType.TEXT) is not None
    # Cannot unregister TEXT adapter.
    assert registry.unregister(ModalityType.TEXT) is False
    # List modalities includes all four registered.
    assert set(registry.list_modalities()) >= {
        ModalityType.TEXT,
        ModalityType.VOICE,
        ModalityType.IMAGE,
        ModalityType.DOCUMENT,
    }


# -------------------------------------------------------------------
# 4. Image adapter raises if PIL not installed
# -------------------------------------------------------------------


def test_image_adapter_raises_if_not_installed():
    adapter = ImageAdapter(pil_module="definitely_not_installed_pil_module_xyz")
    assert adapter.is_ready is False

    # Encoding a bytes payload still works (just wraps raw bytes).
    payload = asyncio.run(adapter.encode(b"\x89PNG\r\n\x1a\n"))
    assert payload.modality == ModalityType.IMAGE

    # Encoding a string reference also works (no PIL needed).
    ref_payload = asyncio.run(adapter.encode("/tmp/image.png"))
    assert ref_payload.metadata.get("ref") is True
    decoded = asyncio.run(adapter.decode(ref_payload))
    assert decoded == "/tmp/image.png"

    # Decoding binary image bytes requires PIL.
    binary_payload = ModalityPayload(
        modality=ModalityType.IMAGE,
        mime_type="image/png",
        encoding="binary",
        data=b"\x89PNG\r\n\x1a\nfake",
    )
    with pytest.raises(ModalityNotConfiguredError):
        asyncio.run(adapter.decode(binary_payload))


# -------------------------------------------------------------------
# Extra: DocumentAdapter encodes plain text always
# -------------------------------------------------------------------


def test_document_adapter_plain_text_always_works():
    adapter = DocumentAdapter()
    payload = asyncio.run(adapter.encode("hello document"))
    assert payload.mime_type == "text/plain"
    text = asyncio.run(adapter.decode(payload))
    assert text == "hello document"


def test_document_adapter_pdf_unavailable_raises():
    # Force a mime-type of PDF so decode tries to invoke pypdf.
    adapter = DocumentAdapter()
    payload = ModalityPayload(
        modality=ModalityType.DOCUMENT,
        mime_type="application/pdf",
        encoding="binary",
        data=b"%PDF-1.4 fake",
    )
    if not adapter.is_pdf_ready():
        with pytest.raises(ModalityNotConfiguredError):
            asyncio.run(adapter.decode(payload))
