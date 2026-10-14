"""INTERNAL: GET /internal/diagnostics — runs Doctor.

This endpoint is INTERNAL — not part of the public API. It is intended
for operators and SREs. It enforces a verified Bearer principal and an
explicit diagnostics:read RBAC grant; network-layer restriction is still
required as defense in depth.
"""

from __future__ import annotations

from typing import Any

from albra_seed.api.context import RequestContext
from albra_seed.api.schemas import DiagnosticsResponse


def build_internal_diagnostics_router(ctx: RequestContext) -> Any:
    """Return a FastAPI APIRouter for internal diagnostics."""
    from fastapi import APIRouter, Header, HTTPException  # lazy

    router = APIRouter(tags=["internal"])

    @router.get(
        "/diagnostics",
        response_model=DiagnosticsResponse,
        responses={
            401: {"description": "Bearer authentication required"},
            403: {"description": "diagnostics:read permission denied"},
            503: {"description": "Authentication/RBAC is not configured"},
        },
    )
    async def diagnostics(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> DiagnosticsResponse:
        from albra_seed.core.types import RiskLevel

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

        policy = ctx.runtime.get_component("policy_engine")
        rule_set = getattr(policy, "rule_set", None)
        permission_registry = getattr(policy, "permission_registry", None)
        if (
            policy is None
            or rule_set is None
            or permission_registry is None
            or not hasattr(permission_registry, "has_any")
            or not hasattr(policy, "evaluate")
        ):
            raise HTTPException(status_code=503, detail="diagnostics authentication/RBAC is not configured")
        action = "diagnostics:read"
        roles = list(getattr(principal, "roles", []) or [])
        try:
            rule = rule_set.first_match(
                action_name=action,
                actor_id=principal.identity,
                risk=RiskLevel.UNKNOWN,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=403, detail="diagnostics policy lookup failed") from exc
        if rule is None or getattr(rule, "required_permission", None) != action:
            raise HTTPException(status_code=403, detail="explicit diagnostics:read policy grant is required")
        try:
            if not permission_registry.has_any(roles, action):
                raise HTTPException(status_code=403, detail="diagnostics:read permission denied")
            verdict = await policy.evaluate(
                action,
                actor_roles=roles,
                actor_id=principal.identity,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=403, detail="diagnostics authorization failed") from exc
        if not getattr(verdict, "allowed", False):
            raise HTTPException(status_code=403, detail="diagnostics action is not authorized")

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
