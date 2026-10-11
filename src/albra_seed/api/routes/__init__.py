"""Routes package — public + internal route builders.

Each module exposes a `build_router(ctx)` factory that returns a
FastAPI `APIRouter`. The factory lazily imports FastAPI so the package
itself can be imported without FastAPI installed.

Two prefixes:
    /api/v1/    — public
    /internal/  — internal
"""

from albra_seed.api.routes.a2a import build_public_a2a_router, build_well_known_router
from albra_seed.api.routes.agent import build_public_agent_router
from albra_seed.api.routes.diagnostics import build_internal_diagnostics_router
from albra_seed.api.routes.execution import build_public_execution_router
from albra_seed.api.routes.health import build_public_health_router
from albra_seed.api.routes.readiness import build_public_readiness_router

__all__ = [
    "build_public_health_router",
    "build_public_readiness_router",
    "build_public_agent_router",
    "build_public_execution_router",
    "build_internal_diagnostics_router",
    "build_public_a2a_router",
    "build_well_known_router",
]
