"""PUBLIC: GET /api/v1/agent/info, GET /api/v1/agent/capabilities."""

from __future__ import annotations

from typing import Any

from albra_seed.api.context import RequestContext
from albra_seed.api.schemas import (
    AgentInfoResponse,
    CapabilitiesResponse,
    CapabilityItem,
    ErrorResponse,
)


def build_public_agent_router(ctx: RequestContext) -> Any:
    """Return a FastAPI APIRouter for the public agent endpoints."""
    from fastapi import APIRouter  # lazy

    router = APIRouter(tags=["agent"])

    @router.get("/agent/info", response_model=AgentInfoResponse)
    async def agent_info() -> AgentInfoResponse:
        agent = ctx.default_agent()
        if agent is None:
            # No agent registered — return a minimal identity-only view.
            return AgentInfoResponse(
                agent_id="seed",
                name="albra-seed",
                version="1.0.0",
                seed_version=ctx.runtime.config.seed_version,
                runtime_version="1.0.0",
            )
        identity = agent.identity
        return AgentInfoResponse(
            agent_id=identity.agent_id,
            name=identity.name,
            version=identity.version,
            seed_version=identity.seed_version,
            runtime_version=identity.runtime_version,
            specialization=identity.specialization,
            capabilities=list(identity.capabilities),
            permissions=list(identity.permissions),
            autonomy_level=identity.autonomy_level.name,
            risk_profile=identity.risk_profile.value,
            providers=list(identity.providers),
            tools=list(identity.tools),
            skills=list(identity.skills),
            memory_domains=list(identity.memory_domains),
            interfaces=list(identity.interfaces),
            dependencies=list(identity.dependencies),
            constraints=list(identity.constraints),
        )

    @router.get(
        "/agent/capabilities",
        response_model=CapabilitiesResponse,
        responses={404: {"model": ErrorResponse}},
    )
    async def agent_capabilities() -> Any:
        agent = ctx.default_agent()
        if agent is None:
            from fastapi import HTTPException  # lazy
            raise HTTPException(
                status_code=404,
                detail="no agent registered with this runtime",
            )
        # Inspect capability registry if registered on the runtime.
        cap_registry = ctx.runtime.get_component("capability_registry")
        items: list[CapabilityItem] = []
        if cap_registry is not None and hasattr(cap_registry, "snapshot"):
            for row in cap_registry.snapshot():
                items.append(CapabilityItem(**row))
        else:
            # Fall back to identity-declared capabilities.
            for name in agent.identity.capabilities:
                items.append(CapabilityItem(name=name))
        return CapabilitiesResponse(capabilities=items, count=len(items))

    return router


__all__ = ["build_public_agent_router"]
