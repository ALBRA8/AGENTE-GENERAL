"""INTERNAL: GET /internal/diagnostics — runs Doctor.

This endpoint is INTERNAL — not part of the public API. It is intended
for operators and SREs. It MUST NOT be exposed to end-users without
additional auth gating (out of scope for the seed).
"""

from __future__ import annotations

from typing import Any

from albra_seed.api.context import RequestContext
from albra_seed.api.schemas import DiagnosticsResponse


def build_internal_diagnostics_router(ctx: RequestContext) -> Any:
    """Return a FastAPI APIRouter for internal diagnostics."""
    from fastapi import APIRouter  # lazy

    router = APIRouter(tags=["internal"])

    @router.get("/diagnostics", response_model=DiagnosticsResponse)
    async def diagnostics() -> DiagnosticsResponse:
        from albra_seed.doctor import Doctor  # lazy import
        doctor = Doctor()
        report = await doctor.diagnose()
        return DiagnosticsResponse(
            generated_at=report.generated_at,
            overall_status=report.overall_status.value,
            summary=report.summary(),
            components=[c.to_dict() for c in report.components],
        )

    return router


__all__ = ["build_internal_diagnostics_router"]
