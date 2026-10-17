"""ASR provider interfaces and implementations.

Supports standard REST OpenAI/Groq/Whisper ASR endpoints, local engines,
and mock implementations for deterministic testing without external dependencies.
Credentials and secrets are strictly redacted from any raised exceptions.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from albra_seed.audio.exceptions import (
    ASRAuthError,
    ASRNotConfiguredError,
    ASRRemoteError,
    ASRTimeoutError,
)
from albra_seed.audio.schemas import TranscriptionResponse


class ASRProvider(ABC):
    """Abstract base class for all ASR transcription providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""

    @abstractmethod
    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.ogg",
        content_type: str = "audio/ogg",
        language: str = "es",
        prompt: str | None = None,
    ) -> TranscriptionResponse:
        """Transcribe audio bytes into text.

        Parameters
        ----------
        audio_bytes : bytes
            Raw audio content.
        filename : str
            Virtual or original filename.
        content_type : str
            MIME type of the audio.
        language : str
            Target language code (default 'es').
        prompt : str | None
            Optional conditioning prompt or context.

        Returns
        -------
        TranscriptionResponse
            Normalized transcription result.
        """


class RestASRProvider(ASRProvider):
    """OpenAI/Whisper/Groq-compatible REST ASR provider.

    Connects to standard HTTP POST endpoints accepting multipart/form-data
    with file, model, and language fields.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        raw_url = (base_url or os.environ.get("ASR_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        if not raw_url.endswith("/audio/transcriptions"):
            self._endpoint_url = f"{raw_url}/audio/transcriptions"
        else:
            self._endpoint_url = raw_url

        self._api_key = api_key or os.environ.get("ASR_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self._model = model or os.environ.get("ASR_MODEL") or "whisper-1"
        self._timeout_seconds = float(timeout_seconds)

    @property
    def name(self) -> str:
        return "rest"

    @property
    def endpoint_url(self) -> str:
        return self._endpoint_url

    @property
    def model(self) -> str:
        return self._model

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.ogg",
        content_type: str = "audio/ogg",
        language: str = "es",
        prompt: str | None = None,
    ) -> TranscriptionResponse:
        if not self._api_key and "127.0.0.1" not in self._endpoint_url and "localhost" not in self._endpoint_url:
            raise ASRNotConfiguredError("ASR API key is missing and target endpoint is not local.")

        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        form_data: dict[str, Any] = {
            "model": self._model,
            "language": language,
            "response_format": "verbose_json",
        }
        if prompt:
            form_data["prompt"] = prompt

        files = {
            "file": (filename, audio_bytes, content_type),
        }

        start_time = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    self._endpoint_url,
                    headers=headers,
                    data=form_data,
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise ASRTimeoutError(
                f"ASR transcription request timed out after {self._timeout_seconds} seconds."
            ) from exc
        except httpx.RequestError as exc:
            raise ASRRemoteError(
                "Failed to connect to remote ASR service."
            ) from exc

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        if response.status_code in (401, 403):
            raise ASRAuthError("Remote ASR service rejected credentials (401/403).")
        if response.is_error:
            raise ASRRemoteError(
                f"Remote ASR service returned HTTP error status {response.status_code}."
            )

        try:
            data = response.json()
        except Exception as exc:
            raise ASRRemoteError("Remote ASR service returned malformed non-JSON response.") from exc

        if not isinstance(data, dict) or "text" not in data:
            raise ASRRemoteError("Remote ASR service response missing expected 'text' field.")

        duration = data.get("duration")
        confidence = None
        if "segments" in data and isinstance(data["segments"], list) and data["segments"]:
            # Approximate average confidence if logprob or no_speech_prob is present
            no_speech = data["segments"][0].get("no_speech_prob")
            if no_speech is not None:
                confidence = max(0.0, min(1.0, 1.0 - float(no_speech)))

        return TranscriptionResponse(
            text=str(data["text"]).strip(),
            language=data.get("language", language),
            duration=float(duration) if duration is not None else None,
            latency_ms=round(latency_ms, 2),
            format=content_type.split("/")[-1] if "/" in content_type else content_type,
            provider="rest",
            model=self._model,
            confidence=confidence,
            metadata={"endpoint": self._endpoint_url.split("//")[-1].split("/")[0]},
        )


class MockASRProvider(ASRProvider):
    """Deterministic mock ASR provider for unit and integration testing."""

    def __init__(
        self,
        transcription_text: str = "Transcripción simulada para prueba.",
        language: str = "es",
        duration: float = 2.5,
        confidence: float = 0.95,
        error_to_raise: Exception | None = None,
    ) -> None:
        self.transcription_text = transcription_text
        self.language = language
        self.duration = duration
        self.confidence = confidence
        self.error_to_raise = error_to_raise

    @property
    def name(self) -> str:
        return "mock"

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.ogg",
        content_type: str = "audio/ogg",
        language: str = "es",
        prompt: str | None = None,
    ) -> TranscriptionResponse:
        if self.error_to_raise is not None:
            raise self.error_to_raise

        return TranscriptionResponse(
            text=self.transcription_text,
            language=language or self.language,
            duration=self.duration,
            latency_ms=1.5,
            format=content_type.split("/")[-1] if "/" in content_type else content_type,
            provider="mock",
            model="mock-asr-v1",
            confidence=self.confidence,
            metadata={"bytes_received": len(audio_bytes), "filename": filename},
        )


def get_default_asr_provider() -> ASRProvider | None:
    """Resolve ASR provider from environment or configuration."""
    provider_type = os.environ.get("ASR_PROVIDER", "").lower().strip()
    if provider_type == "mock":
        return MockASRProvider()
    if provider_type == "none":
        return None

    # Check if explicit ASR_BASE_URL or generic ASR config is present
    asr_key = os.environ.get("ASR_API_KEY")
    asr_url = os.environ.get("ASR_BASE_URL")
    if asr_key or asr_url:
        return RestASRProvider(base_url=asr_url, api_key=asr_key)

    return None


__all__ = [
    "ASRProvider",
    "RestASRProvider",
    "MockASRProvider",
    "get_default_asr_provider",
]
