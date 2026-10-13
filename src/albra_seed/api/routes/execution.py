"""PUBLIC: POST /api/v1/execution, GET /api/v1/execution/{execution_id}.

SECURITY CONTRACT (BLOQ 2 closure):
The POST /execution endpoint calls agent.run() which may execute Tools.
Tool execution requires a security perimeter (firewall + policy) configured
on the Runtime. If the perimeter is absent, the endpoint refuses to execute
and returns HTTP 503 (perimeter not configured).

This contract enforces DEFAULT-DENY at the HTTP boundary:
- CORE_ONLY without perimeter → 503 "security perimeter not configured"
- CORE_ONLY with perimeter → 200 (tools go through firewall + policy)
- Authenticated request with perimeter → 200 (subject to policy decision)

The endpoint does NOT bypass Runtime security; it merely refuses to be an
open door when the perimeter is absent.
"""

from __future__ import annotations

from typing import Any

from albra_seed.api.context import RequestContext
from albra_seed.api.schemas import (
    ErrorResponse,
    ExecutionRequest,
    ExecutionResponse,
    TraceResponse,
)


def _runtime_has_security_perimeter(runtime: Any) -> bool:
    """True iff the Runtime has at least one security gate registered
    (firewall OR policy engine). When BOTH are absent, the runtime is
    in a state where any tool execution would be fail-open — which the
    DEFAULT-DENY contract forbids. The HTTP execution endpoint refuses
    to even call agent.run() in that case.
    """
    firewall = runtime.get_component("firewall")
    policy = runtime.get_component("policy_engine")
    return firewall is not None or policy is not None


def build_public_execution_router(ctx: RequestContext) -> Any:
    """Return a FastAPI APIRouter for the public execution endpoints."""
    from fastapi import APIRouter, Header, HTTPException  # lazy

    router = APIRouter(tags=["execution"])

    async def verified_principal(authorization: str | None) -> Any:
        """Return a trusted authenticated principal or fail closed."""
        if not authorization:
            raise HTTPException(status_code=401, detail="bearer authentication required")
        scheme, separator, credential = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not credential.strip():
            raise HTTPException(status_code=401, detail="invalid authorization header")
        authenticator = ctx.runtime.get_component("authenticator")
        if authenticator is None:
            raise HTTPException(status_code=503, detail="bearer authentication is not configured")
        try:
            principal = await authenticator.verify(credential.strip())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=401, detail="invalid bearer credential") from exc
        if principal is None or getattr(principal, "is_anonymous", True):
            raise HTTPException(status_code=401, detail="invalid bearer credential")
        return principal

    async def require_approval_permission(principal: Any, action: str) -> None:
        """Approval API is default-deny unless a policy explicitly allows it."""
        policy = ctx.runtime.get_component("policy_engine")
        if policy is None or not hasattr(policy, "evaluate"):
            raise HTTPException(status_code=403, detail="approval authorization is not configured")
        # Require a matching rule that names this exact RBAC permission.
        # A generic ALLOW rule is not sufficient for approval administration.
        rule_set = getattr(policy, "rule_set", None)
        if rule_set is None or not hasattr(rule_set, "first_match"):
            raise HTTPException(status_code=403, detail="approval permission policy is not configured")
        from albra_seed.core.types import RiskLevel
        try:
            rule = rule_set.first_match(
                action_name=action,
                actor_id=getattr(principal, "identity", None),
                risk=RiskLevel.UNKNOWN,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=403, detail="approval permission policy failed") from exc
        if rule is None or getattr(rule, "required_permission", None) != action:
            raise HTTPException(status_code=403, detail="explicit approval permission grant is required")
        try:
            verdict = await policy.evaluate(
                action,
                actor_roles=list(getattr(principal, "roles", []) or []),
                actor_id=getattr(principal, "identity", None),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=403, detail="approval authorization failed") from exc
        if not getattr(verdict, "allowed", False):
            raise HTTPException(status_code=403, detail="approval action is not authorized")

    @router.post(
        "/execution",
        response_model=ExecutionResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def execute(
        req: ExecutionRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Any:
        # Forward only a verified Bearer credential to Agent.run().
        token: str | None = None
        principal: Any = None
        if authorization is not None:
            principal = await verified_principal(authorization)
            token = authorization.partition(" ")[2].strip()
        agent = ctx.get_agent(req.agent_id)
        if agent is None:
            raise HTTPException(
                status_code=404,
                detail=f"no agent registered with id={req.agent_id!r}",
            )
        # SECURITY CONTRACT (BLOQ 2): refuse to execute via HTTP when the
        # Runtime has no security perimeter. This prevents the HTTP surface
        # from becoming an open door for tool execution.
        if not _runtime_has_security_perimeter(ctx.runtime):
            raise HTTPException(
                status_code=503,
                detail=(
                    "execution endpoint requires a security perimeter "
                    "(firewall or policy engine) configured on the Runtime. "
                    "Register one before exposing /execution over HTTP."
                ),
            )
        try:
            response = await agent.run(
                req.input,
                timeout_seconds=req.timeout_seconds,
                token=token,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail=f"execution failed: {type(exc).__name__}: {exc}",
            ) from exc
        # Cache the trace for later GET /execution/{id}.
        trace_meta = response.metadata or {}
        trace_steps = list(trace_meta.get("trace", []))
        ctx.store_trace(
            response.execution_id,
            {
                "execution_id": response.execution_id,
                "correlation_id": response.correlation_id,
                "trace": trace_steps,
                "metadata": trace_meta,
                # Stable owner identity comes only from the trusted authenticator.
                # Anonymous executions are never retrievable through GET.
                "owner_identity": getattr(principal, "identity", None),
                "owner_kind": getattr(principal, "kind", None),
            },
        )
        return ExecutionResponse(
            execution_id=response.execution_id,
            correlation_id=response.correlation_id,
            content=response.content,
            confidence=response.confidence,
            tool_calls=[tc.model_dump(mode="json") for tc in response.tool_calls],
            tool_results=[tr.model_dump(mode="json") for tr in response.tool_results],
            evidence_ids=list(response.evidence_ids),
            warnings=list(response.warnings),
            errors=list(response.errors),
            metadata=ctx.redacted(response.metadata or {}),
            trace=trace_steps,
        )

    @router.get(
        "/execution/{execution_id}",
        response_model=TraceResponse,
        responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    async def get_execution(
        execution_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Any:
        principal = await verified_principal(authorization)
        trace = ctx.get_trace(execution_id)
        if trace is None:
            raise HTTPException(
                status_code=404,
                detail=f"no execution trace cached for id={execution_id}",
            )
        if (
            not trace.get("owner_identity")
            or trace.get("owner_identity") != getattr(principal, "identity", None)
            or trace.get("owner_kind") != getattr(principal, "kind", None)
        ):
            raise HTTPException(status_code=403, detail="execution trace access denied")
        return TraceResponse(
            execution_id=execution_id,
            found=True,
            trace=list(trace.get("trace", [])),
            metadata=ctx.redacted(trace.get("metadata", {})),
        )

    @router.get("/approvals/pending")
    async def list_pending_approvals(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Any:
        principal = await verified_principal(authorization)
        await require_approval_permission(principal, "approval:list")
        engine = ctx.runtime.get_component("approval_engine")
        if engine is None or not hasattr(engine, "list_pending"):
            raise HTTPException(status_code=503, detail="approval engine is not configured")
        return {
            "storage_scope": getattr(engine, "storage_scope", "process-local"),
            "approvals": [
                {
                    "approval_id": item.approval_id,
                    "action": item.action,
                    "deadline": item.deadline.isoformat() if item.deadline else None,
                    "execution_id": (item.context_ref or {}).get("execution_id"),
                    "correlation_id": (item.context_ref or {}).get("correlation_id"),
                    "status": "awaiting_approval",
                }
                for item in engine.list_pending()
            ]
        }

    @router.post("/approvals/{approval_id}/decision")
    async def decide_approval(
        approval_id: str,
        payload: dict[str, Any],
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Any:
        principal = await verified_principal(authorization)
        await require_approval_permission(principal, "approval:decide")
        engine = ctx.runtime.get_component("approval_engine")
        if engine is None:
            raise HTTPException(status_code=503, detail="approval engine is not configured")
        decision = payload.get("decision")
        if decision not in {"approve", "reject"}:
            raise HTTPException(status_code=400, detail="decision must be approve or reject")
        request = engine.get(approval_id) if hasattr(engine, "get") else None
        if request is None:
            raise HTTPException(status_code=404, detail="approval not found")
        reason = payload.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 1000:
            raise HTTPException(status_code=400, detail="reason must be a string of at most 1000 characters")
        if decision == "approve":
            if getattr(engine, "storage_scope", "process-local") == "durable_sqlite":
                result = await engine.approve(
                    approval_id, approver=principal.identity, authorized=True
                )
            else:
                result = await engine.approve(approval_id, approver=principal.identity)
        else:
            if getattr(engine, "storage_scope", "process-local") == "durable_sqlite":
                result = await engine.reject(
                    approval_id, approver=principal.identity, reason=reason, authorized=True
                )
            else:
                result = await engine.reject(approval_id, approver=principal.identity, reason=reason)
        return {
            "approval_id": result.approval_id,
            "status": getattr(result.status, "value", result.status),
            "decided_by": result.decided_by,
            "reason": result.reason,
        }

    return router


__all__ = ["build_public_execution_router"]
