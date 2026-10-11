"""PUBLIC: GET /api/v1/health — runtime health snapshot."""

from __future__ import annotations

from typing import Any

from albra_seed.api.context import RequestContext
from albra_seed.api.schemas import HealthResponse


def build_public_health_router(ctx: RequestContext) -> Any:
    """Return a FastAPI APIRouter for the public health endpoint."""
    from fastapi import APIRouter  # lazy

    router = APIRouter(tags=["health"])

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        snap = ctx.runtime.health()
        return HealthResponse(
            status=snap.get("status", "unknown"),
            phase=snap.get("phase", "unknown"),
            core_only=bool(snap.get("core_only", False)),
            components=snap.get("components", {}),
            sessions=int(snap.get("sessions", 0)),
            uptime_seconds=float(snap.get("uptime_seconds", 0.0)),
        )

    return router


__all__ = ["build_public_health_router"]
