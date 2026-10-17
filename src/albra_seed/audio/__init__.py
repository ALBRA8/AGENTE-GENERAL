"""Audio and ASR subpackage for ALBRA SEED."""

from albra_seed.audio.exceptions import (
    ASRAuthError,
    ASRNotConfiguredError,
    ASRRemoteError,
    ASRTimeoutError,
    AudioError,
    AudioFormatNotSupportedError,
    AudioPayloadTooLargeError,
    AudioValidationError,
)
from albra_seed.audio.provider import (
    ASRProvider,
    MockASRProvider,
    RestASRProvider,
    get_default_asr_provider,
)
from albra_seed.audio.schemas import TranscriptionResponse
from albra_seed.audio.validators import (
    DEFAULT_MAX_AUDIO_BYTES,
    SUPPORTED_FORMATS,
    detect_audio_format,
    validate_audio_bytes,
)

__all__ = [
    "AudioError",
    "AudioValidationError",
    "AudioFormatNotSupportedError",
    "AudioPayloadTooLargeError",
    "ASRNotConfiguredError",
    "ASRAuthError",
    "ASRTimeoutError",
    "ASRRemoteError",
    "TranscriptionResponse",
    "ASRProvider",
    "RestASRProvider",
    "MockASRProvider",
    "get_default_asr_provider",
    "SUPPORTED_FORMATS",
    "DEFAULT_MAX_AUDIO_BYTES",
    "detect_audio_format",
    "validate_audio_bytes",
]
