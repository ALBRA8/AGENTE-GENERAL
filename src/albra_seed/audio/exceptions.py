"""Audio and ASR exception hierarchy.

Fail-closed exceptions that sanitize error messages to prevent credential
leakage and clearly distinguish client errors from provider/remote failures.
"""

from __future__ import annotations


class AudioError(Exception):
    """Base exception for all audio subsystem errors."""


class AudioValidationError(AudioError):
    """Raised when an uploaded audio file is invalid, empty, or corrupt."""


class AudioFormatNotSupportedError(AudioValidationError):
    """Raised when the audio format is not supported or magic bytes do not match."""


class AudioPayloadTooLargeError(AudioError):
    """Raised when the audio file size exceeds the configured maximum limit."""


class ASRNotConfiguredError(AudioError):
    """Raised when no ASR provider is configured or available."""


class ASRAuthError(AudioError):
    """Raised when remote ASR provider rejects authentication (401/403)."""


class ASRTimeoutError(AudioError):
    """Raised when remote ASR provider times out."""


class ASRRemoteError(AudioError):
    """Raised when remote ASR provider returns an unrecoverable error or malformed response."""
