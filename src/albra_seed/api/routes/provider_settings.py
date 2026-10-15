"""Provider settings routes — /api/v1/provider/*

Endpoints for reading/writing the provider configuration and testing
the connection. The API key is NEVER returned by any read endpoint.

Endpoints:
    GET  /api/v1/provider/config        — non-sensitive config (no key)
    PUT  /api/v1/provider/config        — save non-sensitive config
    PUT  /api/v1/provider/api-key        — save/replace the API key
    DELETE /api/v1/provider/api-key      — clear the API key
    POST /api/v1/provider/test           — test the connection
    POST /api/v1/provider/reset          — reset to Mock defaults
    GET  /api/v1/provider/defaults       — default configs per provider
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.api.context import RequestContext
from albra_seed.config.settings_store import (
    DEFAULT_CONFIG,
    NVIDIA_DEFAULTS,
    ProviderConfigError,
    SettingsEncryptionError,
    clear_api_key,
    get_api_key,
    get_config,
    record_test_result,
    reset_to_defaults,
    save_api_key,
    save_config,
)


# =====================================================================
# Schemas — defined at module level so FastAPI recognizes them as body
# =====================================================================


class ProviderConfigResponse(BaseModel):
    """Non-sensitive config returned to the dashboard. NEVER includes
    the API key — only ``api_key_set: bool``.
    """
    provider: str = "mock"
    base_url: str = ""
    model: str = ""
    temperature: float = 0.5
    top_p: float = 1.0
    max_tokens: int = 1024
    timeout: float = 60.0
    stream: bool = False
    api_key_set: bool = False
    last_test_at: str | None = None
    last_test_ok: bool | None = None


class ProviderConfigRequest(BaseModel):
    """Request body for PUT /provider/config. Does NOT include the
    API key — that is handled by a separate endpoint.
    """
    provider: str = Field(..., description="mock or nvidia")
    base_url: str = Field(default="")
    model: str = Field(default="")
    temperature: float = Field(default=0.5, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=1024, ge=1, le=32768)
    timeout: float = Field(default=60.0, ge=1.0, le=600.0)
    stream: bool = Field(default=False)


class ApiKeyRequest(BaseModel):
    """Request body for PUT /provider/api-key. The key is encrypted
    at rest and NEVER returned.
    """
    api_key: str = Field(..., min_length=1, description="The provider API key")


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: float | None = None


class ResetResponse(BaseModel):
    ok: bool
    message: str


class ErrorResponse(BaseModel):
    detail: str


def build_provider_settings_router(ctx: RequestContext) -> Any:
    """Return a FastAPI APIRouter for the provider settings endpoints.

    All provider-settings operations require a server-side bearer token
    configured with ALBRA_PROVIDER_SETTINGS_TOKEN. The token is never
    read from query parameters or request bodies.
    """
    import hmac
    import os

    from fastapi import APIRouter, Depends, Header, HTTPException

    async def require_provider_settings_token(
        authorization: str | None = Header(default=None),
    ) -> None:
        """Fail closed unless a configured token matches in constant time."""
        expected = os.environ.get("ALBRA_PROVIDER_SETTINGS_TOKEN", "")
        # A short configured value is not a meaningful bearer secret. Fail
        # closed instead of silently accepting a guessable admin credential.
        if len(expected) < 32:
            raise HTTPException(
                status_code=503,
                detail="Provider settings authentication is not configured securely",
            )
        scheme, _, supplied = (authorization or "").partition(" ")
        if (
            scheme.lower() != "bearer"
            or not supplied
            or not hmac.compare_digest(supplied, expected)
        ):
            raise HTTPException(
                status_code=401,
                detail="Valid bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    router = APIRouter(
        tags=["provider-settings"],
        dependencies=[Depends(require_provider_settings_token)],
    )

    # ------------------------------------------------------------------ #
    # Routes                                                             #
    # ------------------------------------------------------------------ #

    @router.get(
        "/provider/config",
        response_model=ProviderConfigResponse,
        responses={500: {"model": ErrorResponse}},
    )
    async def get_provider_config() -> Any:
        """Return the non-sensitive provider config. NEVER returns the
        API key — only ``api_key_set: bool``.
        """
        try:
            cfg = get_config()
            return ProviderConfigResponse(**cfg)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"failed to read config: {type(exc).__name__}",
            )

    @router.put(
        "/provider/config",
        response_model=ProviderConfigResponse,
        responses={
            400: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    async def put_provider_config(req: ProviderConfigRequest) -> Any:
        """Save the non-sensitive provider config. Does NOT touch the
        API key — if a key is already saved, it is preserved.
        """
        try:
            save_config(
                provider=req.provider,
                base_url=req.base_url,
                model=req.model,
                temperature=req.temperature,
                top_p=req.top_p,
                max_tokens=req.max_tokens,
                timeout=req.timeout,
                stream=req.stream,
            )
        except ProviderConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except SettingsEncryptionError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"failed to save config: {type(exc).__name__}",
            )

        cfg = get_config()
        return ProviderConfigResponse(**cfg)

    @router.put(
        "/provider/api-key",
        responses={
            400: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    async def put_api_key(req: ApiKeyRequest) -> Any:
        """Encrypt and save the API key. The key is encrypted with
        Fernet using ``SETTINGS_ENCRYPTION_KEY``. If the env var is
        not set, returns 500 with a clear message.
        """
        try:
            save_api_key(req.api_key)
        except SettingsEncryptionError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        except ProviderConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"failed to save api key: {type(exc).__name__}",
            )

        return {"ok": True, "message": "API key saved (encrypted at rest)"}

    @router.delete("/provider/api-key")
    async def delete_api_key() -> Any:
        """Remove the API key from the store. The non-sensitive config
        is preserved.
        """
        try:
            clear_api_key()
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"failed to clear api key: {type(exc).__name__}",
            )

        return {"ok": True, "message": "API key cleared"}

    @router.post(
        "/provider/test",
        response_model=TestConnectionResponse,
        responses={
            400: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
        },
    )
    async def test_connection() -> Any:
        """Test the provider connection by making a minimal completion
        request. Does NOT execute a real task or activate tools.

        Requires the provider to be configured (provider, model,
        base_url, api_key). Returns ok=True if the provider responds,
        ok=False with a safe message if it fails.
        """
        import time as _time

        cfg = get_config()

        if cfg["provider"] == "mock":
            record_test_result(True)
            return TestConnectionResponse(
                ok=True,
                message="Mock provider is always available (offline mode)",
                latency_ms=0.0,
            )

        if cfg["provider"] == "nvidia":
            if not cfg["base_url"] or not cfg["model"]:
                record_test_result(False)
                raise HTTPException(
                    status_code=400,
                    detail="Provider not fully configured: base_url and model are required",
                )

            api_key = get_api_key()
            if not api_key:
                record_test_result(False)
                raise HTTPException(
                    status_code=400,
                    detail="API key not configured. Set it via PUT /provider/api-key",
                )

            try:
                from albra_seed.providers.nvidia_provider import NVIDIAProvider
                provider = NVIDIAProvider(
                    api_key=api_key,
                    model=cfg["model"],
                    base_url=cfg["base_url"],
                    timeout=min(cfg["timeout"], 30.0),
                    temperature=0.0,
                    max_tokens=1,
                    stream=False,
                )

                from albra_seed.core.types import Message, Role
                t0 = _time.monotonic()
                response = await provider.complete(
                    [Message(role=Role.USER, content="ping")],
                )
                latency_ms = (_time.monotonic() - t0) * 1000.0

                if response.errors:
                    record_test_result(False)
                    safe_msg = response.errors[0]
                    import re
                    safe_msg = re.sub(
                        r"nvapi-[A-Za-z0-9_\-.]{8,}", "nvapi-[REDACTED]", safe_msg
                    )
                    safe_msg = re.sub(
                        r"Bearer\s+[A-Za-z0-9_\-.]{8,}", "Bearer [REDACTED]", safe_msg
                    )
                    return TestConnectionResponse(
                        ok=False,
                        message=f"Provider returned an error: {safe_msg}",
                        latency_ms=round(latency_ms, 2),
                    )

                record_test_result(True)
                return TestConnectionResponse(
                    ok=True,
                    message="Connection successful — provider responded to a minimal request",
                    latency_ms=round(latency_ms, 2),
                )
            except HTTPException:
                raise
            except Exception as exc:
                record_test_result(False)
                safe_type = type(exc).__name__
                return TestConnectionResponse(
                    ok=False,
                    message=f"Connection failed: {safe_type} (see server logs for details)",
                )

        record_test_result(False)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {cfg['provider']!r}. Supported: mock, nvidia.",
        )

    @router.post(
        "/provider/reset",
        response_model=ResetResponse,
    )
    async def reset_config() -> Any:
        """Reset the provider config to Mock defaults. Clears the API
        key too.
        """
        try:
            reset_to_defaults()
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"failed to reset: {type(exc).__name__}",
            )

        return ResetResponse(
            ok=True,
            message="Config reset to Mock defaults; API key cleared",
        )

    @router.get("/provider/defaults")
    async def get_defaults() -> Any:
        """Return the default configs for each supported provider.
        Does NOT include any API key — just the non-sensitive defaults.
        """
        return {
            "mock": DEFAULT_CONFIG,
            "nvidia": NVIDIA_DEFAULTS,
        }

    return router


__all__ = ["build_provider_settings_router"]
