"""PUBLIC: POST /api/v1/execution, GET /api/v1/execution/{execution_id}."""

from __future__ import annotations

from typing import Any

from albra_seed.api.context import RequestContext
from albra_seed.api.schemas import (
    ErrorResponse,
    ExecutionRequest,
    ExecutionResponse,
    TraceResponse,
)


def build_public_execution_router(ctx: RequestContext) -> Any:
    """Return a FastAPI APIRouter for the public execution endpoints."""
    from fastapi import APIRouter, HTTPException  # lazy

    router = APIRouter(tags=["execution"])

    @router.post(
        "/execution",
        response_model=ExecutionResponse,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    async def execute(req: ExecutionRequest) -> Any:
        agent = ctx.get_agent(req.agent_id)
        if agent is None:
            raise HTTPException(
                status_code=404,
                detail=f"no agent registered with id={req.agent_id!r}",
            )
        try:
            response = await agent.run(
                req.input,
                timeout_seconds=req.timeout_seconds,
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
        responses={404: {"model": ErrorResponse}},
    )
    async def get_execution(execution_id: str) -> Any:
        trace = ctx.get_trace(execution_id)
        if trace is None:
            raise HTTPException(
                status_code=404,
                detail=f"no execution trace cached for id={execution_id}",
            )
        return TraceResponse(
            execution_id=execution_id,
            found=True,
            trace=list(trace.get("trace", [])),
            metadata=ctx.redacted(trace.get("metadata", {})),
        )

    return router


__all__ = ["build_public_execution_router"]
