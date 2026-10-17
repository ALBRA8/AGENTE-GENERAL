"""create_app(runtime) — build a FastAPI app wired to a Runtime.

FastAPI is LAZY-imported: calling `create_app` requires fastapi to be
installed. Importing this module does NOT require it. If fastapi is not
installed, `create_app` raises a clean ImportError with installation
instructions.

Public routes mount under `/api/v1/`; internal routes under `/internal/`.
"""

from __future__ import annotations

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
    """Create a FastAPI app bound to `runtime`.

    Parameters
    ----------
    runtime : Runtime
        The runtime whose health/readiness/agent info is exposed.
    config : ConfigManager | None
        Optional pre-built config manager. If None, one is constructed
        from `runtime.config`.
    agents : dict[str, Any] | None
        Optional initial agent registry keyed by agent_id.
    title, version : str
        API metadata shown by /docs.
    a2a_server : Any | None
        Optional A2AServer. When provided, the A2A JSON-RPC route
        (``POST /a2a/v1/``) and the discovery route
        (``GET /.well-known/agent-card.json``) are mounted. When None
        (CORE_ONLY), only the well-known route is mounted and it
        returns HTTP 404. This keeps the A2A HTTP layer optional —
        importing the package does not require it.
    """
    fastapi = _require_fastapi()
    FastAPI = fastapi.FastAPI
    ctx = RequestContext(
        runtime, config=config, agents=agents, a2a_server=a2a_server
    )
    app = FastAPI(title=title, version=version)
    app.state.ctx = ctx

    # PUBLIC routes — /api/v1/*
    from albra_seed.api.routes import (
        build_public_agent_router,
        build_public_audio_router,
        build_public_execution_router,
        build_public_health_router,
        build_public_readiness_router,
    )
    app.include_router(build_public_health_router(ctx), prefix="/api/v1")
    app.include_router(build_public_readiness_router(ctx), prefix="/api/v1")
    app.include_router(build_public_agent_router(ctx), prefix="/api/v1")
    app.include_router(build_public_execution_router(ctx), prefix="/api/v1")
    app.include_router(build_public_audio_router(ctx), prefix="/api/v1")

    # INTERNAL routes — /internal/*
    from albra_seed.api.routes import build_internal_diagnostics_router
    app.include_router(build_internal_diagnostics_router(ctx), prefix="/internal")

    # A2A routes — always mount the well-known router (returns 404 when
    # no server is configured); mount the JSON-RPC router only when an
    # A2AServer is provided (CORE_ONLY safe).
    from albra_seed.api.routes.a2a import (
        build_public_a2a_router,
        build_well_known_router,
    )
    app.include_router(build_well_known_router(ctx))
    if a2a_server is not None:
        app.include_router(build_public_a2a_router(ctx))

    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:  # pragma: no cover - trivial
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
