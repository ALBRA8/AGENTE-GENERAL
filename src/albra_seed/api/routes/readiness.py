"""PUBLIC: GET /api/v1/readiness — bool readiness probe."""

from __future__ import annotations

from typing import Any

from albra_seed.api.context import RequestContext
from albra_seed.api.schemas import ReadinessResponse
from albra_seed.runtime.lifecycle import LifecycleManager  # type-only reference


def build_public_readiness_router(ctx: RequestContext) -> Any:
    """Return a FastAPI APIRouter for the public readiness endpoint."""
    from fastapi import APIRouter  # lazy

    router = APIRouter(tags=["readiness"])

    @router.get("/readiness", response_model=ReadinessResponse)
    async def readiness() -> ReadinessResponse:
        ready = ctx.runtime.readiness()
        phase = ctx.runtime.lifecycle.phase.value
        return ReadinessResponse(ready=ready, phase=phase)

    return router


__all__ = ["build_public_readiness_router"]
