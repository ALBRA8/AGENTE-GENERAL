"""create_app(runtime) — build a FastAPI app wired to a Runtime.

Public routes mount under /api/v1/; internal routes under /internal/.
Cross-origin browser access is disabled unless ALBRA_CORS_ORIGINS explicitly
lists trusted origins (comma-separated exact origins).
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

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
    for origin in origins:
        parsed_origin = urlsplit(origin)
        is_local_http = (
            parsed_origin.scheme == "http"
            and parsed_origin.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        try:
            port = parsed_origin.port
        except ValueError as exc:
            raise ValueError("ALBRA_CORS_ORIGINS contains an invalid origin") from exc
        if (
            origin == "*"
            or parsed_origin.scheme not in {"https", "http"}
            or not parsed_origin.hostname
            or (parsed_origin.scheme != "https" and not is_local_http)
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or (port is not None and not (1 <= port <= 65535))
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise ValueError(
                "ALBRA_CORS_ORIGINS must contain exact HTTPS origins "
                "(HTTP is allowed only for localhost); wildcards are forbidden"
            )
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

    # Enforce a bounded body even when Content-Length is absent or dishonest.
    # Provider settings are small JSON documents; 1 MiB is ample.
    @app.middleware("http")
    async def limit_request_body(request: Request, call_next: Any) -> Any:
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) < 0:
                    return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
                if int(length) > 1_048_576:
                    return JSONResponse(status_code=413, content={"detail": "Request body too large"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})

        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 1_048_576:
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})
            body.extend(chunk)
        request._body = bytes(body)
        body_sent = False

        async def receive_buffered() -> dict[str, Any]:
            nonlocal body_sent
            if body_sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            body_sent = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        request._receive = receive_buffered
        return await call_next(request)

    # Conservative process-local backstop for provider-settings abuse. Production
    # must additionally enforce shared/distributed limits at the trusted edge.
    from collections import defaultdict, deque
    from time import monotonic

    rate_windows: dict[tuple[str, str], deque[float]] = defaultdict(deque)
    rate_window_seconds = 60.0
    max_rate_buckets = 10_000

    def _positive_int_env(name: str, default: int) -> int:
        raw = os.environ.get(name, str(default))
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive integer") from exc
        if value < 1:
            raise ValueError(f"{name} must be a positive integer")
        return value

    provider_read_limit = _positive_int_env("ALBRA_PROVIDER_RATE_LIMIT_PER_MINUTE", 60)
    provider_write_limit = _positive_int_env("ALBRA_PROVIDER_WRITE_RATE_LIMIT_PER_MINUTE", 10)

    @app.middleware("http")
    async def limit_provider_settings_rate(request: Request, call_next: Any) -> Any:
        if not request.url.path.startswith("/api/v1/provider/"):
            return await call_next(request)
        client_host = request.client.host if request.client else "unknown"
        is_write = request.method in {"PUT", "POST", "PATCH", "DELETE"}
        limit = provider_write_limit if is_write else provider_read_limit
        bucket = (client_host, "write" if is_write else "read")
        now = monotonic()
        # Expire inactive buckets so spoofed/rotating client addresses cannot
        # grow the in-memory map forever. Cap active keys as a final backstop.
        expired = [
            key for key, timestamps in rate_windows.items()
            if not timestamps or now - timestamps[-1] >= rate_window_seconds
        ]
        for key in expired:
            rate_windows.pop(key, None)
        if bucket not in rate_windows and len(rate_windows) >= max_rate_buckets:
            oldest_key = min(
                rate_windows,
                key=lambda key: rate_windows[key][-1] if rate_windows[key] else float("-inf"),
            )
            rate_windows.pop(oldest_key, None)
        window = rate_windows[bucket]
        while window and now - window[0] >= rate_window_seconds:
            window.popleft()
        if len(window) >= limit:
            retry_after = max(1, int(rate_window_seconds - (now - window[0])))
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many provider-settings requests"},
                headers={"Retry-After": str(retry_after), "Cache-Control": "no-store"},
            )
        window.append(now)
        return await call_next(request)

    # PUBLIC routes — /api/v1/*
    from albra_seed.api.routes import (
        build_public_agent_router,
        build_public_audio_router,
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
