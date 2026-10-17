"""Audio format and content validation utilities.

Inspects raw audio byte signatures (magic numbers) and size constraints
to guarantee fail-closed security and reject malformed/unsupported files
before forwarding to any downstream ASR provider.
"""

from __future__ import annotations

from typing import Tuple

from albra_seed.audio.exceptions import (
    AudioFormatNotSupportedError,
    AudioPayloadTooLargeError,
    AudioValidationError,
)

# Supported audio format identifiers
SUPPORTED_FORMATS = frozenset({"ogg", "opus", "mp3", "wav", "m4a", "flac", "webm"})

# Default file size limit: 25 MB
DEFAULT_MAX_AUDIO_BYTES = 25 * 1024 * 1024


def detect_audio_format(data: bytes) -> str | None:
    """Detect audio format from magic bytes (file signature).

    Returns the detected format string (e.g. 'ogg', 'wav', 'mp3')
    or None if the signature is not recognized.
    """
    if len(data) < 4:
        return None

    # Ogg container (standard for WhatsApp Opus voice notes)
    if data.startswith(b"OggS"):
        return "ogg"

    # WAV container: RIFF....WAVE
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"

    # MP3 with ID3v2 container
    if data.startswith(b"ID3"):
        return "mp3"

    # MP3 raw frame sync: 11 bits set (0xFFE0 mask)
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "mp3"

    # M4A / AAC (ISO BMFF with 'ftyp' at offset 4)
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return "m4a"

    # FLAC format
    if data.startswith(b"fLaC"):
        return "flac"

    # WebM / Matroska EBML header
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"

    return None


def validate_audio_bytes(
    data: bytes,
    filename: str | None = None,
    max_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
) -> Tuple[str, str]:
    """Validate audio bytes against size constraints and header signatures.

    Parameters
    ----------
    data : bytes
        Raw audio content to validate.
    filename : str | None
        Optional uploaded filename for fallback MIME detection.
    max_bytes : int
        Maximum permitted file size in bytes.

    Returns
    -------
    Tuple[str, str]
        (detected_format, mime_type)

    Raises
    ------
    AudioValidationError
        If file is empty or too short.
    AudioPayloadTooLargeError
        If file exceeds max_bytes.
    AudioFormatNotSupportedError
        If audio signature is unrecognized or unsupported.
    """
    if not data or len(data) == 0:
        raise AudioValidationError("Audio file is empty (0 bytes).")

    if len(data) > max_bytes:
        raise AudioPayloadTooLargeError(
            f"Audio file size ({len(data)} bytes) exceeds the maximum allowed limit of {max_bytes} bytes."
        )

    detected_format = detect_audio_format(data)
    if detected_format is None:
        raise AudioFormatNotSupportedError(
            "Unsupported or unrecognized audio format. Magic bytes do not match supported "
            f"formats: {', '.join(sorted(SUPPORTED_FORMATS))}."
        )

    mime_map = {
        "ogg": "audio/ogg",
        "opus": "audio/ogg",
        "wav": "audio/wav",
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "flac": "audio/flac",
        "webm": "audio/webm",
    }
    mime_type = mime_map.get(detected_format, "application/octet-stream")
    return detected_format, mime_type


__all__ = [
    "SUPPORTED_FORMATS",
    "DEFAULT_MAX_AUDIO_BYTES",
    "detect_audio_format",
    "validate_audio_bytes",
]
