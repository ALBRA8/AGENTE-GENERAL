"""Public audio ingestion and ASR transcription endpoint.

POST /api/v1/audio/transcribe receives an audio file (e.g. OGG/Opus from WhatsApp,
WAV, MP3) via multipart/form-data, validates file magic bytes and size constraints,
enforces authentication/RBAC authorization, delegates to the configured ASR provider,
and returns the transcribed text and metadata without modifying or triggering text agent chat.
"""

from __future__ import annotations

import os
from typing import Any

from albra_seed.api.context import RequestContext
from albra_seed.api.schemas import ErrorResponse
from albra_seed.audio.exceptions import (
    ASRAuthError,
    ASRNotConfiguredError,
    ASRRemoteError,
    ASRTimeoutError,
    AudioFormatNotSupportedError,
    AudioPayloadTooLargeError,
    AudioValidationError,
)
from albra_seed.audio.provider import get_default_asr_provider
from albra_seed.audio.schemas import TranscriptionResponse
from albra_seed.audio.validators import DEFAULT_MAX_AUDIO_BYTES, validate_audio_bytes

try:
    from fastapi import UploadFile  # type: ignore[import-untyped]
except ImportError:
    UploadFile = Any  # type: ignore[misc,assignment]


# Bounded upload read: read at most `limit` bytes from the upload stream
# without ever buffering the entire file in memory. Used by the transcribe
# endpoint to enforce ASR_MAX_FILE_BYTES before the audio is forwarded to
# the provider. The function does NOT rely on Content-Length — it reads
# the stream in small chunks and stops as soon as `limit` is reached.
_AUDIO_READ_CHUNK = 64 * 1024  # 64 KiB chunks


async def _read_bounded(file: Any, limit: int) -> bytes:
    """Read at most `limit` bytes from an upload stream.

    Returns the bytes read (length 0..limit). If the returned length equals
    `limit`, the caller knows the upload is at least `limit` bytes long and
    can decide to reject it as oversized.
    """
    buf = bytearray()
    remaining = int(limit)
    while remaining > 0:
        chunk_size = min(_AUDIO_READ_CHUNK, remaining)
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        buf.extend(chunk)
        remaining -= len(chunk)
    return bytes(buf)


def build_public_audio_router(ctx: RequestContext) -> Any:
    """Return a FastAPI APIRouter for audio transcription endpoints."""
    from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile  # lazy

    router = APIRouter(tags=["audio"])

    async def verified_principal(authorization: str | None) -> Any:
        """Authenticate API callers; anonymous/missing credentials fail closed."""
        if not authorization:
            raise HTTPException(status_code=401, detail="bearer authentication required")
        scheme, separator, credential = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not credential.strip():
            raise HTTPException(status_code=401, detail="invalid authorization header")
        authenticator = ctx.runtime.get_component("authenticator")
        if authenticator is None:
            raise HTTPException(status_code=503, detail="authentication is not configured")
        try:
            principal = await authenticator.verify(credential.strip())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=401, detail="invalid bearer credential") from exc
        if (
            principal is None
            or getattr(principal, "is_anonymous", True)
            or not getattr(principal, "identity", None)
        ):
            raise HTTPException(status_code=401, detail="authenticated principal required")
        return principal

    async def require_transcribe_permission(principal: Any) -> None:
        """Require explicit RBAC policy permission for audio transcription.

        Fail-closed policy: if ANY authorization component is missing or
        raises an unexpected error, access is denied. We never silently
        allow the request through when the security perimeter is
        incomplete — doing so would let an unauthenticated or
        under-privileged caller reach the ASR provider.
        """
        from albra_seed.core.types import RiskLevel  # noqa: F401  # re-exported symbol kept for callers

        policy = ctx.runtime.get_component("policy_engine")
        if policy is None:
            # No policy engine registered → security perimeter is incomplete.
            # Fail closed with 503 (missing essential security dependency)
            # rather than allowing the request to reach the ASR provider.
            raise HTTPException(
                status_code=503,
                detail="Authorization policy engine is not configured.",
            )

        rule_set = getattr(policy, "rule_set", None)
        permission_registry = getattr(policy, "permission_registry", None)
        if rule_set is None or permission_registry is None:
            # Policy engine exists but its rule_set / permission_registry
            # are not wired → still fail closed. We do NOT trust the
            # authenticator alone for authorization decisions.
            raise HTTPException(
                status_code=503,
                detail="Authorization rule_set or permission_registry is not configured.",
            )

        roles = list(getattr(principal, "roles", []) or [])
        # Accept either dedicated audio:transcribe or general execution:run
        # as the explicit permission required for this action. Both must
        # pass through the policy engine — a missing permission never
        # short-circuits to allow.
        allowed = False
        evaluation_error: Exception | None = None
        for action in ("audio:transcribe", "execution:run"):
            if not hasattr(permission_registry, "has_any"):
                # PermissionRegistry missing the required method → fail closed.
                raise HTTPException(
                    status_code=503,
                    detail="Permission registry does not implement has_any().",
                )
            if not permission_registry.has_any(roles, action):
                continue
            try:
                verdict = await policy.evaluate(
                    action,
                    actor_roles=roles,
                    actor_id=principal.identity,
                )
                if getattr(verdict, "allowed", False):
                    allowed = True
                    break
            except HTTPException:
                raise
            except Exception as exc:  # noqa: BLE001
                # Record but DO NOT swallow — we will deny below unless
                # a subsequent action explicitly returns allowed=True.
                evaluation_error = exc
                continue

        if not allowed:
            # If we hit an unexpected error AND no action explicitly allowed,
            # surface 503 (so operators can distinguish a missing/broken
            # authorization component from a clean 403 denial).
            if evaluation_error is not None:
                raise HTTPException(
                    status_code=503,
                    detail="Authorization policy evaluation failed.",
                )
            raise HTTPException(
                status_code=403,
                detail="audio:transcribe permission denied",
            )

    @router.post(
        "/audio/transcribe",
        response_model=TranscriptionResponse,
        responses={
            400: {"model": ErrorResponse, "description": "Audio validation error or unsupported format"},
            401: {"model": ErrorResponse, "description": "Authentication required"},
            403: {"model": ErrorResponse, "description": "Permission denied"},
            413: {"model": ErrorResponse, "description": "Audio payload too large"},
            502: {"model": ErrorResponse, "description": "Remote ASR provider error"},
            503: {"model": ErrorResponse, "description": "ASR provider not configured"},
            504: {"model": ErrorResponse, "description": "Remote ASR provider timeout"},
        },
    )
    async def transcribe_audio(
        file: UploadFile = File(..., description="Audio file payload (OGG/Opus, WAV, MP3, etc.)"),
        language: str = Form(default="es", description="ISO language code (default 'es')"),
        prompt: str | None = Form(default=None, description="Optional prompt context for the ASR model"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Any:
        # 1. Authenticate caller
        principal = await verified_principal(authorization)
        await require_transcribe_permission(principal)

        # 2. Read and validate audio payload with bounded memory.
        #
        # Read at most (max_bytes + 1) bytes from the upload stream so we
        # can detect over-sized payloads WITHOUT loading the entire file
        # into memory. `Content-Length` is used only as an early-reject
        # hint — the canonical check is the bounded read itself because
        # clients may omit or spoof the header.
        max_bytes_env = os.environ.get("ASR_MAX_FILE_BYTES")
        try:
            max_bytes = int(max_bytes_env) if max_bytes_env else DEFAULT_MAX_AUDIO_BYTES
        except ValueError:
            max_bytes = DEFAULT_MAX_AUDIO_BYTES
        if max_bytes <= 0:
            max_bytes = DEFAULT_MAX_AUDIO_BYTES

        # Early reject: if Content-Length is present and already exceeds the
        # limit, fail before reading the body. This is a defence-in-depth
        # check; the bounded read below is the authoritative enforcement.
        try:
            declared_size = file.size  # Starlette exposes Content-Length here
        except Exception:  # noqa: BLE001
            declared_size = None
        if declared_size is not None and declared_size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Audio file size ({declared_size} bytes) exceeds the maximum allowed limit of {max_bytes} bytes.",
            )

        # Bounded read: read up to (max_bytes + 1) bytes. If we read
        # exactly (max_bytes + 1) bytes the upload is too large and we
        # reject it before forwarding to the provider.
        try:
            content = await _read_bounded(file, max_bytes + 1)
        except Exception as exc:  # noqa: BLE001
            try:
                await file.close()
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(status_code=400, detail="Failed to read uploaded audio file.") from exc

        if len(content) > max_bytes:
            try:
                await file.close()
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=413,
                detail=f"Audio file size exceeds the maximum allowed limit of {max_bytes} bytes.",
            )

        try:
            detected_format, mime_type = validate_audio_bytes(
                content,
                filename=file.filename,
                max_bytes=max_bytes,
            )
        except AudioValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AudioFormatNotSupportedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AudioPayloadTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

        # 3. Resolve ASR provider
        asr_provider = ctx.runtime.get_component("asr_provider") or get_default_asr_provider()
        if asr_provider is None:
            raise HTTPException(
                status_code=503,
                detail="ASR provider is not configured. Configure ASR_BASE_URL/ASR_API_KEY or register an ASRProvider component.",
            )

        # 4. Transcribe audio
        try:
            filename = file.filename or f"audio.{detected_format}"
            response = await asr_provider.transcribe(
                audio_bytes=content,
                filename=filename,
                content_type=mime_type,
                language=language,
                prompt=prompt,
            )
            # Ensure detected format is populated if not provided
            if not response.format:
                response.format = detected_format
            return response
        except ASRTimeoutError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        except ASRAuthError as exc:
            raise HTTPException(status_code=502, detail="Remote ASR provider authentication failed.") from exc
        except ASRRemoteError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ASRNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            # Redact raw exception details to prevent credential/internal leaks
            raise HTTPException(
                status_code=500,
                detail=f"Transcription processing error: {type(exc).__name__}",
            ) from exc

    return router


__all__ = ["build_public_audio_router"]
