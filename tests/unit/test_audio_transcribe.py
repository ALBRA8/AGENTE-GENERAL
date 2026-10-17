"""Unit tests for the audio ingestion and ASR transcription subsystem."""

from __future__ import annotations

import io
from typing import Any
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from albra_seed.api.app import create_app
from albra_seed.audio.exceptions import (
    ASRAuthError,
    ASRNotConfiguredError,
    ASRRemoteError,
    ASRTimeoutError,
    AudioFormatNotSupportedError,
    AudioPayloadTooLargeError,
    AudioValidationError,
)
from albra_seed.audio.provider import MockASRProvider, RestASRProvider
from albra_seed.audio.validators import detect_audio_format, validate_audio_bytes
from albra_seed.core.config import SeedConfig
from albra_seed.core.types import PolicyDecision, RiskLevel
from albra_seed.policy.engine import PolicyEngine
from albra_seed.policy.rules import PolicyRule, PolicyRuleSet
from albra_seed.runtime.runtime import Runtime
from albra_seed.security.auth import AuthPrincipal
from albra_seed.security.permissions import Permission, PermissionRegistry
from albra_seed.security.roles import Role, RoleRegistry


# Sample valid headers for different audio containers
OGG_HEADER = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00" + b"\x00" * 30
WAV_HEADER = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 20
MP3_ID3_HEADER = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 20
MP3_SYNC_HEADER = b"\xff\xfb\x90\x64" + b"\x00" * 20
FLAC_HEADER = b"fLaC\x00\x00\x00\x22" + b"\x00" * 20
WEBM_HEADER = b"\x1a\x45\xdf\xa3\x01\x00\x00\x00" + b"\x00" * 20


# =====================================================================
# 1. Validator Tests
# =====================================================================


def test_detect_audio_format():
    assert detect_audio_format(OGG_HEADER) == "ogg"
    assert detect_audio_format(WAV_HEADER) == "wav"
    assert detect_audio_format(MP3_ID3_HEADER) == "mp3"
    assert detect_audio_format(MP3_SYNC_HEADER) == "mp3"
    assert detect_audio_format(FLAC_HEADER) == "flac"
    assert detect_audio_format(WEBM_HEADER) == "webm"
    assert detect_audio_format(b"TEXT_NOT_AUDIO") is None
    assert detect_audio_format(b"") is None


def test_validate_audio_bytes_success():
    fmt, mime = validate_audio_bytes(OGG_HEADER, "voice_note.ogg")
    assert fmt == "ogg"
    assert mime == "audio/ogg"

    fmt, mime = validate_audio_bytes(WAV_HEADER, "sample.wav")
    assert fmt == "wav"
    assert mime == "audio/wav"


def test_validate_audio_bytes_empty():
    with pytest.raises(AudioValidationError, match="Audio file is empty"):
        validate_audio_bytes(b"", "empty.ogg")


def test_validate_audio_bytes_unsupported():
    with pytest.raises(AudioFormatNotSupportedError, match="Unsupported or unrecognized"):
        validate_audio_bytes(b"This is just a text file renamed to audio.ogg", "fake.ogg")


def test_validate_audio_bytes_oversized():
    with pytest.raises(AudioPayloadTooLargeError, match="exceeds the maximum allowed limit"):
        validate_audio_bytes(OGG_HEADER + b"\x00" * 1000, "large.ogg", max_bytes=50)


# =====================================================================
# 2. Provider Unit Tests
# =====================================================================


@pytest.mark.asyncio
async def test_mock_asr_provider_success():
    provider = MockASRProvider(transcription_text="Hola mundo", language="es", duration=3.0)
    res = await provider.transcribe(OGG_HEADER, "test.ogg", "audio/ogg", language="es")
    assert res.text == "Hola mundo"
    assert res.language == "es"
    assert res.duration == 3.0
    assert res.provider == "mock"
    assert res.latency_ms > 0


@pytest.mark.asyncio
async def test_mock_asr_provider_error():
    provider = MockASRProvider(error_to_raise=ASRTimeoutError("Simulated timeout"))
    with pytest.raises(ASRTimeoutError, match="Simulated timeout"):
        await provider.transcribe(OGG_HEADER, "test.ogg", "audio/ogg")


def test_rest_asr_provider_url_resolution():
    p1 = RestASRProvider(base_url="https://api.openai.com/v1", api_key="test-key")
    assert p1.endpoint_url == "https://api.openai.com/v1/audio/transcriptions"

    p2 = RestASRProvider(base_url="https://api.openai.com/v1/audio/transcriptions", api_key="test-key")
    assert p2.endpoint_url == "https://api.openai.com/v1/audio/transcriptions"


# =====================================================================
# 3. API Integration Tests
# =====================================================================


class _TestAuthenticator:
    async def verify(self, token: str | None):
        if token == "valid-token":
            return AuthPrincipal(
                kind="user",
                identity="user-1",
                roles=["transcriber"],
                auth_method="test",
                is_anonymous=False,
            )
        if token == "no-role-token":
            return AuthPrincipal(
                kind="user",
                identity="guest-1",
                roles=["guest"],
                auth_method="test",
                is_anonymous=False,
            )
        return None


def _setup_runtime(configure_asr: bool = True, asr_provider: Any = None) -> Runtime:
    cfg = SeedConfig()
    rt = Runtime(config=cfg)

    # Authenticator
    rt.register("authenticator", _TestAuthenticator())

    # RBAC & Policy
    roles = RoleRegistry()
    roles.register(Role(role_id="transcriber", name="Transcriber"))
    roles.register(Role(role_id="guest", name="Guest"))
    permissions = PermissionRegistry(roles)
    permissions.register(Permission(id="audio:transcribe"))
    permissions.grant("transcriber", "audio:transcribe")

    rules = PolicyRuleSet()
    rules.add(
        PolicyRule(
            name="allow audio transcribe",
            action_pattern="audio:transcribe",
            actor_pattern="*",
            risk_threshold=RiskLevel.UNKNOWN,
            required_permission="audio:transcribe",
            decision=PolicyDecision.ALLOW,
        )
    )
    policy = PolicyEngine(rule_set=rules, permission_registry=permissions)
    rt.register("policy_engine", policy)

    if configure_asr:
        rt.register("asr_provider", asr_provider or MockASRProvider(transcription_text="Audio transcrito correctamente."))

    return rt


def test_transcribe_missing_auth():
    rt = _setup_runtime()
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    res = client.post("/api/v1/audio/transcribe", files=files)
    assert res.status_code == 401
    assert "bearer authentication required" in res.json()["detail"]


def test_transcribe_invalid_token():
    rt = _setup_runtime()
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer invalid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 401


def test_transcribe_permission_denied():
    rt = _setup_runtime()
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer no-role-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 403
    assert "permission denied" in res.json()["detail"]


def test_transcribe_empty_file():
    rt = _setup_runtime()
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(b""), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 400
    assert "Audio file is empty" in res.json()["detail"]


def test_transcribe_corrupt_file():
    rt = _setup_runtime()
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(b"Hello from not-an-audio-file"), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 400
    assert "Unsupported or unrecognized audio format" in res.json()["detail"]


def test_transcribe_success_ogg():
    mock_p = MockASRProvider(transcription_text="Mensaje de voz de WhatsApp recibido.")
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers, data={"language": "es"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["text"] == "Mensaje de voz de WhatsApp recibido."
    assert payload["language"] == "es"
    assert payload["format"] == "ogg"
    assert payload["provider"] == "mock"
    assert payload["latency_ms"] > 0


def test_transcribe_success_wav():
    mock_p = MockASRProvider(transcription_text="Audio WAV procesado.")
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("recording.wav", io.BytesIO(WAV_HEADER), "audio/wav")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 200
    payload = res.json()
    assert payload["text"] == "Audio WAV procesado."
    assert payload["format"] == "wav"


def test_transcribe_unconfigured_asr():
    rt = _setup_runtime(configure_asr=False)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 503
    assert "ASR provider is not configured" in res.json()["detail"]


def test_transcribe_provider_timeout():
    mock_p = MockASRProvider(error_to_raise=ASRTimeoutError("Remote ASR request timed out after 30.0s"))
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 504
    assert "timed out" in res.json()["detail"]


def test_transcribe_provider_remote_error():
    mock_p = MockASRProvider(error_to_raise=ASRRemoteError("Remote ASR service returned HTTP error status 500"))
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 502
    assert "HTTP error status 500" in res.json()["detail"]
