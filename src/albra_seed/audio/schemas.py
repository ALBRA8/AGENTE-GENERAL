"""Pydantic schemas for audio transcription requests and responses."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class TranscriptionResponse(BaseModel):
    """Normalized response payload for audio transcription."""

    text: str = Field(..., description="Transcribed text in the target language")
    language: str = Field(default="es", description="Language code used for transcription")
    duration: float | None = Field(default=None, description="Audio duration in seconds if available")
    latency_ms: float = Field(default=0.0, description="Processing latency in milliseconds")
    format: str | None = Field(default=None, description="Detected audio container/codec format")
    provider: str | None = Field(default=None, description="Provider used for transcription")
    model: str | None = Field(default=None, description="Model used for transcription")
    confidence: float | None = Field(default=None, description="Average confidence score if provided")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional provider metadata")


__all__ = ["TranscriptionResponse"]
