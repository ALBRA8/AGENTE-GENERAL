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
        """Require explicit RBAC policy permission for audio transcription."""
        from albra_seed.core.types import RiskLevel

        policy = ctx.runtime.get_component("policy_engine")
        if policy is None:
            return  # No policy engine configured; authenticator check is sufficient

        rule_set = getattr(policy, "rule_set", None)
        permission_registry = getattr(policy, "permission_registry", None)
        if rule_set is None or permission_registry is None:
            return

        roles = list(getattr(principal, "roles", []) or [])
        # Accept either dedicated audio:transcribe or general execution:run
        allowed = False
        for action in ("audio:transcribe", "execution:run"):
            if hasattr(permission_registry, "has_any") and permission_registry.has_any(roles, action):
                try:
                    verdict = await policy.evaluate(
                        action,
                        actor_roles=roles,
                        actor_id=principal.identity,
                    )
                    if getattr(verdict, "allowed", False):
                        allowed = True
                        break
                except Exception:  # noqa: BLE001
                    pass

        if not allowed:
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

        # 2. Read and validate audio payload
        try:
            content = await file.read()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="Failed to read uploaded audio file.") from exc

        max_bytes_env = os.environ.get("ASR_MAX_FILE_BYTES")
        try:
            max_bytes = int(max_bytes_env) if max_bytes_env else DEFAULT_MAX_AUDIO_BYTES
        except ValueError:
            max_bytes = DEFAULT_MAX_AUDIO_BYTES

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
