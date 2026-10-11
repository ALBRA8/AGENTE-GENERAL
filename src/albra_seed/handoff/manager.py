"""Handoff manager — initiates and receives handoffs between ALBRA agents.

The manager:
  * routes a `HandoffRequest` to the registered handler for `target`;
  * enforces the deadline via `HandoffTimeout`;
  * records evidence in a `HandoffEvidenceChain`;
  * records every handoff in an audit sink (optional).

The manager does NOT know about other transport mechanisms — it always
calls the in-process handler registered via `AgentRegistry`. Specialisation
layers may wrap this manager with a network transport without touching
the contract / evidence / timeout types.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from albra_seed.core.types import new_id, utc_now
from albra_seed.handoff.contract import (
    HandoffRequest,
    HandoffResponse,
    HandoffStatus,
)
from albra_seed.handoff.evidence import HandoffEvidenceChain
from albra_seed.handoff.registry import AgentRegistry, HandoffHandler
from albra_seed.handoff.timeout import HandoffTimeout

# Audit sink is an async callable (dict) -> None.
AuditSink = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class _ActiveHandoff:
    request: HandoffRequest
    timeout: HandoffTimeout
    started_at: Any = field(default_factory=utc_now)


class HandoffManager:
    """Coordinates handoffs to other ALBRA agents."""

    def __init__(
        self,
        registry: AgentRegistry | None = None,
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.registry = registry or AgentRegistry()
        self.evidence_chain = HandoffEvidenceChain()
        self._audit_sink = audit_sink
        self._active: dict[str, _ActiveHandoff] = {}

    # -- initiate ----------------------------------------------------
    async def initiate(self, request: HandoffRequest) -> HandoffResponse:
        """Hand off to another ALBRA agent. Returns the final response."""
        if not request.execution_id:
            raise ValueError("execution_id is required")
        if request.requester == request.target:
            return HandoffResponse(
                execution_id=request.execution_id,
                status=HandoffStatus.REJECTED,
                errors=["requester and target are the same agent"],
                responder=request.target,
            )
        # Pre-flight: reject if already expired.
        timeout = HandoffTimeout(request, audit_sink=self._audit_sink)
        if timeout.is_expired():
            resp = timeout.fire("handoff already expired before dispatch")
            await self._record_evidence(request, resp)
            await self._audit(request, resp, phase="initiate-expired")
            return resp

        handler = self.registry.resolve_handler(request.target)
        if handler is None:
            resp = HandoffResponse(
                execution_id=request.execution_id,
                status=HandoffStatus.REJECTED,
                errors=[f"target agent not available: {request.target}"],
                responder=request.target,
            )
            await self._record_evidence(request, resp)
            await self._audit(request, resp, phase="initiate-rejected")
            return resp

        self._active[request.execution_id] = _ActiveHandoff(
            request=request, timeout=timeout
        )
        try:
            remaining = timeout.remaining_seconds()
            if remaining is not None:
                response = await asyncio.wait_for(
                    handler(request), timeout=remaining
                )
            else:
                response = await handler(request)
        except asyncio.TimeoutError:
            response = timeout.fire()
            await timeout.maybe_audit()
        except Exception as exc:
            response = HandoffResponse.failure(
                request.execution_id,
                errors=[f"handler raised: {exc}"],
                responder=request.target,
            )
        finally:
            self._active.pop(request.execution_id, None)

        await self._record_evidence(request, response)
        await self._audit(request, response, phase="initiate-complete")
        return response

    # -- receive -----------------------------------------------------
    async def receive(
        self,
        request: HandoffRequest,
        handler: HandoffHandler,
    ) -> HandoffResponse:
        """Receive a handoff from another agent and dispatch to `handler`.

        Used by the receiving agent's runtime; it enforces the same
        deadline + evidence semantics as `initiate`."""
        timeout = HandoffTimeout(request, audit_sink=self._audit_sink)
        if timeout.is_expired():
            resp = timeout.fire("handoff received after deadline")
            await self._record_evidence(request, resp)
            await self._audit(request, resp, phase="receive-expired")
            return resp
        try:
            remaining = timeout.remaining_seconds()
            if remaining is not None:
                response = await asyncio.wait_for(
                    handler(request), timeout=remaining
                )
            else:
                response = await handler(request)
        except asyncio.TimeoutError:
            response = timeout.fire()
            await timeout.maybe_audit()
        except Exception as exc:
            response = HandoffResponse.failure(
                request.execution_id,
                errors=[f"handler raised: {exc}"],
                responder=request.target,
            )
        await self._record_evidence(request, response)
        await self._audit(request, response, phase="receive-complete")
        return response

    # -- introspection -----------------------------------------------
    def active_handoffs(self) -> list[str]:
        return list(self._active.keys())

    # -- helpers -----------------------------------------------------
    async def _record_evidence(
        self, request: HandoffRequest, response: HandoffResponse
    ) -> None:
        self.evidence_chain.append(
            evidence_id=new_id("h-ev"),
            request=request,
            response=response,
        )

    async def _audit(
        self,
        request: HandoffRequest,
        response: HandoffResponse,
        *,
        phase: str,
    ) -> None:
        if self._audit_sink is None:
            return
        entry = {
            "phase": phase,
            "execution_id": request.execution_id,
            "requester": request.requester,
            "target": request.target,
            "status": response.status,
            "confidence": response.confidence,
            "errors": list(response.errors),
            "warnings": list(response.warnings),
            "timestamp": utc_now().isoformat(),
        }
        try:
            await self._audit_sink(entry)
        except Exception:
            pass


__all__ = ["HandoffManager", "AuditSink"]
