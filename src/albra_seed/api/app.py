"""create_app(runtime) — build a FastAPI app wired to a Runtime.

Public routes mount under /api/v1/; internal routes under /internal/.
Cross-origin browser access is disabled unless ALBRA_CORS_ORIGINS explicitly
lists trusted origins (comma-separated exact origins).
"""

from __future__ import annotations

import os
from typing import Any

from albra_seed.api.context import RequestContext
from albra_seed.config import ConfigManager
from albra_seed.runtime.runtime import Runtime


def _require_fastapi() -> Any:
    try:
        import fastapi  # type: ignore[import-untyped]
        return fastapi
    except ImportError as exc:  # pragma: no cover - optional dep
        raise ImportError(
            "fastapi is required for create_app(); install with "
            "`pip install 'albra-seed[api]'` or `pip install fastapi uvicorn`."
        ) from exc


def create_app(
    runtime: Runtime,
    *,
    config: ConfigManager | None = None,
    agents: dict[str, Any] | None = None,
    title: str = "ALBRA SEED API",
    version: str = "1.0.0",
    a2a_server: Any = None,
) -> Any:
    """Create a FastAPI app bound to `runtime`."""
    fastapi = _require_fastapi()
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    ctx = RequestContext(runtime, config=config, agents=agents, a2a_server=a2a_server)
    app = fastapi.FastAPI(title=title, version=version)
    app.state.ctx = ctx

    # Never let FastAPI/Pydantic echo submitted values (including oversized
    # API keys) into validation responses.
    @app.exception_handler(RequestValidationError)
    async def sanitized_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        safe_errors = []
        for error in exc.errors():
            safe_errors.append({
                "loc": list(error.get("loc", ())),
                "msg": str(error.get("msg", "Invalid request")),
                "type": str(error.get("type", "value_error")),
            })
        return JSONResponse(status_code=422, content={"detail": safe_errors})

    # Exact origin allowlist only. No wildcard, no reflected Origin, and no
    # cookies/credentials: the dashboard authenticates with an explicit token.
    origins = [
        item.strip().rstrip("/")
        for item in os.environ.get("ALBRA_CORS_ORIGINS", "").split(",")
        if item.strip()
    ]
    if origins:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            max_age=600,
        )

    # Bound request body size before JSON parsing to limit memory/CPU abuse.
    # Provider settings are small JSON documents; 1 MiB is ample.
    @app.middleware("http")
    async def limit_request_body(request: Request, call_next: Any) -> Any:
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > 1_048_576:
                    return JSONResponse(status_code=413, content={"detail": "Request body too large"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
        return await call_next(request)

    # PUBLIC routes — /api/v1/*
    from albra_seed.api.routes import (
        build_public_agent_router,
        build_public_execution_router,
        build_public_health_router,
        build_public_readiness_router,
        build_provider_settings_router,
    )
    app.include_router(build_public_health_router(ctx), prefix="/api/v1")
    app.include_router(build_public_readiness_router(ctx), prefix="/api/v1")
    app.include_router(build_public_agent_router(ctx), prefix="/api/v1")
    app.include_router(build_public_execution_router(ctx), prefix="/api/v1")
    app.include_router(build_provider_settings_router(ctx), prefix="/api/v1")

    # INTERNAL routes — diagnostics implement their own bearer + RBAC checks.
    from albra_seed.api.routes import build_internal_diagnostics_router
    app.include_router(build_internal_diagnostics_router(ctx), prefix="/internal")

    from albra_seed.api.routes.a2a import (
        build_public_a2a_router,
        build_well_known_router,
    )
    app.include_router(build_well_known_router(ctx))
    if a2a_server is not None:
        app.include_router(build_public_a2a_router(ctx))

    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:  # pragma: no cover
        return {
            "name": "albra-seed-api",
            "version": version,
            "docs": "/docs",
            "public_prefix": "/api/v1",
            "internal_prefix": "/internal",
            "a2a_prefix": "/a2a/v1" if a2a_server is not None else "",
        }

    return app


__all__ = ["create_app"]
