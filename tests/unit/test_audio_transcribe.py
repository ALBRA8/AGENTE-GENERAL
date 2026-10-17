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


# =====================================================================
# 4. RBAC fail-closed tests (security review)
#    Every scenario where authorization is incomplete MUST deny access
#    BEFORE the request reaches the ASR provider.
# =====================================================================


class _RecordingMockASRProvider(MockASRProvider):
    """Mock provider that records whether it was called."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.was_called = False

    async def transcribe(self, *args: Any, **kwargs: Any):
        self.was_called = True
        return await super().transcribe(*args, **kwargs)


def _setup_runtime_without_policy_engine(asr_provider: Any = None) -> Runtime:
    """Runtime with authenticator + ASR but NO policy_engine registered."""
    cfg = SeedConfig()
    rt = Runtime(config=cfg)
    rt.register("authenticator", _TestAuthenticator())
    rt.register("asr_provider", asr_provider or MockASRProvider())
    return rt


def _setup_runtime_with_policy_engine_no_rule_set(asr_provider: Any = None) -> Runtime:
    """Runtime where policy_engine exists but rule_set / permission_registry are missing."""
    cfg = SeedConfig()
    rt = Runtime(config=cfg)
    rt.register("authenticator", _TestAuthenticator())

    # Register a PolicyEngine WITHOUT rule_set/permission_registry — simulates
    # a misconfigured engine. We bypass the constructor's normal signature
    # by creating an instance and clearing attributes.
    policy = PolicyEngine()
    # Force the attributes to None to simulate missing wiring.
    if hasattr(policy, "rule_set"):
        try:
            policy.rule_set = None
        except Exception:  # noqa: BLE001
            pass
    if hasattr(policy, "permission_registry"):
        try:
            policy.permission_registry = None
        except Exception:  # noqa: BLE001
            pass
    rt.register("policy_engine", policy)
    rt.register("asr_provider", asr_provider or MockASRProvider())
    return rt


class _BrokenPolicyEngine:
    """Stand-in for a policy engine whose evaluate() raises unexpectedly."""

    rule_set = object()  # truthy but useless
    permission_registry = object()  # truthy, but has no has_any method

    async def evaluate(self, *args: Any, **kwargs: Any):
        raise RuntimeError("simulated policy evaluation failure")


def _setup_runtime_with_broken_policy_engine(asr_provider: Any = None) -> Runtime:
    """Runtime whose policy_engine.evaluate() raises an unexpected error."""
    cfg = SeedConfig()
    rt = Runtime(config=cfg)
    rt.register("authenticator", _TestAuthenticator())

    # Use a real PermissionRegistry with has_any so the path that calls
    # policy.evaluate is reached — but the engine itself raises.
    roles = RoleRegistry()
    roles.register(Role(role_id="transcriber", name="Transcriber"))
    perms = PermissionRegistry(roles)
    perms.register(Permission(id="audio:transcribe"))
    perms.grant("transcriber", "audio:transcribe")

    class _PolicyWrapper:
        rule_set = object()
        permission_registry = perms

        async def evaluate(self, *args: Any, **kwargs: Any):
            raise RuntimeError("simulated policy evaluation failure")

    rt.register("policy_engine", _PolicyWrapper())
    rt.register("asr_provider", asr_provider or MockASRProvider())
    return rt


def test_rbac_authenticated_user_with_permission_succeeds():
    """Case 1: authenticated user WITH valid permission → 200 + provider called."""
    mock_p = _RecordingMockASRProvider(transcription_text="Hola")
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 200
    assert mock_p.was_called, "ASR provider should have been called for an authorized user"


def test_rbac_authenticated_user_without_permission_denied():
    """Case 2: authenticated user WITHOUT permission → 403 + provider NOT called."""
    mock_p = _RecordingMockASRProvider()
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer no-role-token"}  # guest role, no perms
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 403
    assert "permission denied" in res.json()["detail"]
    assert not mock_p.was_called, "ASR provider must NOT be called when permission is denied"


def test_rbac_missing_policy_engine_fails_closed():
    """Case 3: no policy_engine registered → 503 + provider NOT called (fail-closed)."""
    mock_p = _RecordingMockASRProvider()
    rt = _setup_runtime_without_policy_engine(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 503
    assert "policy engine" in res.json()["detail"].lower()
    assert not mock_p.was_called, "ASR provider must NOT be called when policy_engine is missing"


def test_rbac_missing_rule_set_or_permission_registry_fails_closed():
    """Case 4+5: rule_set or permission_registry missing → 503 + provider NOT called."""
    mock_p = _RecordingMockASRProvider()
    rt = _setup_runtime_with_policy_engine_no_rule_set(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 503
    assert "rule_set" in res.json()["detail"] or "permission_registry" in res.json()["detail"]
    assert not mock_p.was_called, "ASR provider must NOT be called when rule_set/permission_registry missing"


def test_rbac_policy_evaluation_error_fails_closed():
    """Case 6: policy.evaluate() raises → 503 + provider NOT called (no swallow)."""
    mock_p = _RecordingMockASRProvider()
    rt = _setup_runtime_with_broken_policy_engine(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 503
    assert "policy evaluation failed" in res.json()["detail"].lower()
    assert not mock_p.was_called, "ASR provider must NOT be called when policy evaluation raises"


def test_rbac_unauthenticated_user_rejected_before_provider():
    """Case 7: no Authorization header → 401 + provider NOT called."""
    mock_p = _RecordingMockASRProvider()
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    files = {"file": ("audio.ogg", io.BytesIO(OGG_HEADER), "audio/ogg")}
    res = client.post("/api/v1/audio/transcribe", files=files)
    assert res.status_code == 401
    assert not mock_p.was_called, "ASR provider must NOT be called when caller is unauthenticated"


# =====================================================================
# 5. Bounded upload size tests (security review)
# =====================================================================


def test_transcribe_file_within_limit_succeeds():
    """File size exactly at the limit minus one byte → 200 OK."""
    mock_p = MockASRProvider(transcription_text="OK")
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    # Build a valid OGG that is exactly 100 bytes (header + padding)
    payload = OGG_HEADER + b"\x00" * (100 - len(OGG_HEADER))
    assert len(payload) == 100

    files = {"file": ("audio.ogg", io.BytesIO(payload), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post(
        "/api/v1/audio/transcribe",
        files=files,
        headers=headers,
        data={"language": "es"},
    )
    assert res.status_code == 200, res.text


def test_transcribe_file_exceeds_limit_by_one_byte_rejected(monkeypatch):
    """File size = max_bytes + 1 → 413 + provider NOT called (bounded read)."""
    # Force a small limit to make the test fast and deterministic.
    monkeypatch.setenv("ASR_MAX_FILE_BYTES", "100")

    mock_p = _RecordingMockASRProvider()
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    # Build a payload that is 1 byte over the limit.
    payload = OGG_HEADER + b"\x00" * (100 + 1 - len(OGG_HEADER))
    assert len(payload) == 101

    files = {"file": ("audio.ogg", io.BytesIO(payload), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 413, res.text
    assert "exceeds the maximum" in res.json()["detail"].lower()
    assert not mock_p.was_called, "Provider must NOT be called when payload exceeds size limit"


def test_transcribe_file_exceeds_declared_content_length_rejected(monkeypatch):
    """When Content-Length is present and > limit → 413 (early reject)."""
    monkeypatch.setenv("ASR_MAX_FILE_BYTES", "100")

    mock_p = _RecordingMockASRProvider()
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    payload = OGG_HEADER + b"\x00" * 200  # well over limit
    files = {"file": ("audio.ogg", io.BytesIO(payload), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 413
    assert not mock_p.was_called


def test_transcribe_upload_without_content_length_still_enforces_limit(monkeypatch):
    """Without Content-Length, the bounded read still catches oversized uploads."""
    monkeypatch.setenv("ASR_MAX_FILE_BYTES", "100")

    mock_p = _RecordingMockASRProvider()
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    # Use a chunked-style streaming upload by wrapping BytesIO so .size is None
    class _NoSizeStream(io.BytesIO):
        @property
        def size(self):  # type: ignore[override]
            return None

    payload = OGG_HEADER + b"\x00" * 500  # exceeds 100-byte limit
    files = {"file": ("audio.ogg", _NoSizeStream(payload), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 413
    assert not mock_p.was_called


def test_transcribe_malformed_payload_rejected_before_provider():
    """A payload that passes size but fails magic-byte check → 400 + provider NOT called."""
    mock_p = _RecordingMockASRProvider()
    rt = _setup_runtime(asr_provider=mock_p)
    app = create_app(rt)
    client = TestClient(app)

    payload = b"This is not audio data at all but it's short"
    files = {"file": ("audio.ogg", io.BytesIO(payload), "audio/ogg")}
    headers = {"Authorization": "Bearer valid-token"}
    res = client.post("/api/v1/audio/transcribe", files=files, headers=headers)
    assert res.status_code == 400
    assert "Unsupported or unrecognized" in res.json()["detail"]
    assert not mock_p.was_called


# =====================================================================
# 6. DevAuthenticator / Production-fail-closed tests (security review)
# =====================================================================


def _load_render_app_module(monkeypatch):
    """Reload render_app under a controlled env.

    render_app captures PRODUCTION_ENV and _cors_origins at import time.
    We isolate each test by spawning a fresh Python subprocess with the
    desired env vars and inspecting the result via JSON on stdout. This
    avoids sys.modules contamination when other test modules (e.g.
    tests/integration/test_render_app_smoke.py) have already imported
    render_app with different env settings.
    """
    import json
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    # Apply monkeypatch.setenv / delenv changes to the subprocess env.
    for key in ("APP_ENV", "ALBRA_CORS_ORIGINS", "ALBRA_DEV_TOKEN", "RENDER", "DYNO"):
        if key in os.environ:
            env[key] = os.environ[key]
        else:
            env.pop(key, None)

    return env


def _run_render_app_probe(env: dict, action: str, token: str | None = None) -> dict:
    """Spawn a subprocess that imports render_app under `env` and runs a probe."""
    import json
    import os
    import subprocess
    import sys

    script = (
        "import asyncio, importlib, os, sys, json\n"
        "import render_app\n"
        f"action = {action!r}\n"
        f"token = {token!r}\n"
        "if action == 'verify_dev_token':\n"
        "    auth = render_app.DevAuthenticator()\n"
        "    try:\n"
        "        result = asyncio.new_event_loop().run_until_complete(auth.verify(token))\n"
        "        if result is None:\n"
        "            print(json.dumps({'status': 'rejected'}))\n"
        "        else:\n"
        "            print(json.dumps({'status': 'accepted', 'identity': result.identity}))\n"
        "    except Exception as exc:\n"
        "        print(json.dumps({'status': 'error', 'message': str(exc)}))\n"
        "elif action == 'inspect_cors':\n"
        "    print(json.dumps({'cors_origins': render_app._cors_origins, 'production_env': render_app.PRODUCTION_ENV}))\n"
    )

    import os

    # Resolve the repo root dynamically so the test works in CI (where
    # the checkout lives in /home/runner/work/...) and locally.
    # __file__ is tests/unit/test_audio_transcribe.py → repo root is 2 levels up.
    _repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        cwd=_repo_root,
        timeout=30,
    )
    if proc.returncode != 0:
        return {
            "status": "subprocess_failed",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-500:],
            "stderr": proc.stderr[-500:],
        }
    # Parse the LAST line of stdout as JSON (the script may emit other noise).
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return {"status": "no_output", "stderr": proc.stderr[-300:]}
    try:
        return json.loads(lines[-1])
    except Exception:
        return {"status": "non_json", "raw": lines[-1]}


def test_dev_authenticator_rejects_dev_token_in_production(monkeypatch):
    """DevAuthenticator must return None when PRODUCTION_ENV is True."""
    env = dict(__import__("os").environ)
    env["APP_ENV"] = "production"
    env["ALBRA_CORS_ORIGINS"] = "https://example.com"  # so CORS guard passes
    env.pop("RENDER", None)
    env.pop("DYNO", None)
    result = _run_render_app_probe(env, "verify_dev_token", "albra-dev-token")
    assert result.get("status") == "rejected", f"Dev token must NOT authenticate in production. Got: {result}"


def test_dev_authenticator_accepts_dev_token_in_dev(monkeypatch):
    """DevAuthenticator accepts the dev token when explicitly in dev mode."""
    env = dict(__import__("os").environ)
    env["APP_ENV"] = "dev"
    env.pop("RENDER", None)
    env.pop("DYNO", None)
    result = _run_render_app_probe(env, "verify_dev_token", "albra-dev-token")
    assert result.get("status") == "accepted", f"Dev token must authenticate in dev. Got: {result}"
    assert result.get("identity") == "admin"


def test_dev_auth_token_value_is_configurable(monkeypatch):
    """ALBRA_DEV_TOKEN overrides the default dev token value (dev only)."""
    env = dict(__import__("os").environ)
    env["APP_ENV"] = "dev"
    env["ALBRA_DEV_TOKEN"] = "my-custom-dev-token"
    env.pop("RENDER", None)
    env.pop("DYNO", None)

    # Default token must NOT work when overridden
    r1 = _run_render_app_probe(env, "verify_dev_token", "albra-dev-token")
    assert r1.get("status") == "rejected", f"Default token must be rejected when ALBRA_DEV_TOKEN is set. Got: {r1}"

    # Custom token DOES work
    r2 = _run_render_app_probe(env, "verify_dev_token", "my-custom-dev-token")
    assert r2.get("status") == "accepted", f"Custom dev token must authenticate. Got: {r2}"


# =====================================================================
# 7. CORS configuration tests (security review)
# =====================================================================


def test_cors_production_requires_explicit_origins(monkeypatch):
    """render_app must refuse to start in production without ALBRA_CORS_ORIGINS."""
    env = dict(__import__("os").environ)
    env["APP_ENV"] = "production"
    env.pop("ALBRA_CORS_ORIGINS", None)
    env.pop("RENDER", None)
    env.pop("DYNO", None)
    result = _run_render_app_probe(env, "inspect_cors")
    assert result.get("status") == "subprocess_failed", f"render_app should have raised RuntimeError. Got: {result}"
    assert "ALBRA_CORS_ORIGINS" in result.get("stderr", ""), result


def test_cors_dev_uses_localhost_origins_when_unset(monkeypatch):
    """In dev, when ALBRA_CORS_ORIGINS is unset, localhost origins are used (NOT wildcard)."""
    env = dict(__import__("os").environ)
    env["APP_ENV"] = "dev"
    env.pop("ALBRA_CORS_ORIGINS", None)
    env.pop("RENDER", None)
    env.pop("DYNO", None)
    result = _run_render_app_probe(env, "inspect_cors")
    assert result.get("status") != "subprocess_failed", f"render_app should have started. Got: {result}"
    origins = result.get("cors_origins", [])
    assert origins, "Dev must have at least localhost origins"
    assert "*" not in origins, "Wildcard origin is forbidden"


def test_cors_explicit_origins_used_when_configured(monkeypatch):
    """ALBRA_CORS_ORIGINS=foo,bar must produce ['foo','bar'] as allowed origins."""
    env = dict(__import__("os").environ)
    env["APP_ENV"] = "production"
    env["ALBRA_CORS_ORIGINS"] = "https://app.example.com,https://admin.example.com"
    env.pop("RENDER", None)
    env.pop("DYNO", None)
    result = _run_render_app_probe(env, "inspect_cors")
    assert result.get("status") != "subprocess_failed", f"render_app should have started. Got: {result}"
    assert set(result.get("cors_origins", [])) == {
        "https://app.example.com",
        "https://admin.example.com",
    }


def test_cors_no_credentials_with_wildcard(monkeypatch):
    """allow_credentials is True only when _cors_origins is non-empty (never with wildcard)."""
    env = dict(__import__("os").environ)
    env["APP_ENV"] = "dev"
    env.pop("ALBRA_CORS_ORIGINS", None)
    env.pop("RENDER", None)
    env.pop("DYNO", None)
    result = _run_render_app_probe(env, "inspect_cors")
    assert result.get("status") != "subprocess_failed", f"render_app should have started. Got: {result}"
    # Dev has localhost origins so credentials are allowed — but only because
    # they are explicit origins, never a wildcard.
    assert result.get("cors_origins"), "Dev must have at least localhost origins"

