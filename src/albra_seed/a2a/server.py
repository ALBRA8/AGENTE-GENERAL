"""A2AServer — receives inbound A2A tasks and dispatches them through
the existing HandoffManager.

Pipeline (per receive()):

  1. Idempotency check — if task_id is already known and terminal,
     return the stored result without re-executing.
  2. Malformed / deadline pre-checks.
  3. Security filter — A2ASecurityFilter runs the full enforcement
     pipeline (auth → firewall → policy → HITL → autonomy).
  4. If denied or REQUIRE_HITL → task state set accordingly, no
     execution, audit + provenance still recorded.
  5. If allowed → translate to HandoffRequest via A2AAdapter.
  6. HandoffManager.receive() dispatches to the handler.
  7. Translate the HandoffResponse back to an A2ATask via the adapter.
  8. Audit, provenance, evidence are recorded for EVERY receive call.

The server itself carries no domain knowledge. It is a thin translator
+ dispatcher on top of the existing handoff / security / audit /
provenance / evidence stack.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from albra_seed.a2a.adapter import A2AAdapter
from albra_seed.a2a.card import AgentCard, manifest_to_card
from albra_seed.a2a.contract import A2ATask, A2ATaskStatus, TaskState
from albra_seed.a2a.observers import (
    record_audit,
    register_evidence,
    track_input_provenance,
    track_output_provenance,
)
from albra_seed.a2a.security import A2ASecurityFilter
from albra_seed.core.identity import AgentIdentity, AgentManifest
from albra_seed.core.types import utc_now
from albra_seed.handoff.contract import HandoffRequest, HandoffResponse
from albra_seed.handoff.manager import HandoffManager

# A handler takes a HandoffRequest and returns a HandoffResponse.
HandoffHandlerFn = Callable[[HandoffRequest], Awaitable[HandoffResponse]]


def _default_handler(target: str) -> HandoffHandlerFn:
    """Build a no-op handler that echoes the objective back.

    Used when the server is constructed without an explicit handler.
    The handler returns a SUCCEEDED response with the objective text as
    the result, so CORE_ONLY mode produces a working end-to-end flow
    without any specialization code.
    """

    async def handler(req: HandoffRequest) -> HandoffResponse:
        return HandoffResponse.success(
            req.execution_id,
            result={"echo": req.objective, "received_by": target},
            confidence=0.5,
            responder=target,
        )

    return handler


class A2AServer:
    """A2A server — receives inbound tasks and dispatches to HandoffManager.

    The server stores every received task in an internal dict keyed by
    task_id. This is the basis for:
      * idempotency (re-submit returns the stored result)
      * status queries (`get_status`)
      * cancellation (`cancel`)
    """

    def __init__(
        self,
        runtime: Any,
        *,
        identity: AgentIdentity,
        handoff_manager: HandoffManager,
        handler: HandoffHandlerFn | None = None,
    ) -> None:
        self._runtime = runtime
        self._identity = identity
        self._handoff_manager = handoff_manager
        self._handler: HandoffHandlerFn = handler or _default_handler(
            identity.agent_id
        )
        self._adapter = A2AAdapter(runtime=runtime)
        self._security = A2ASecurityFilter(runtime)
        self._tasks: dict[str, A2ATask] = {}

    # -----------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------
    def get_agent_card(self, *, endpoint: str | None = None) -> AgentCard:
        """Build an A2A AgentCard from this server's identity."""
        manifest = AgentManifest(identity=self._identity)
        return manifest_to_card(manifest, endpoint=endpoint)

    @property
    def identity(self) -> AgentIdentity:
        return self._identity

    # -----------------------------------------------------------------
    # Task lifecycle
    # -----------------------------------------------------------------
    async def receive(
        self,
        task: A2ATask,
        *,
        token: str | None = None,
    ) -> A2ATask:
        """Receive an inbound A2A task (see module docstring for pipeline)."""
        # 1. Idempotency: terminal tasks return the stored result.
        existing = self._tasks.get(task.task_id)
        if existing is not None and existing.is_terminal:
            return existing

        # 2. Malformed / deadline pre-checks.
        if task.message is None and not task.objective:
            failed = task.model_copy(deep=True)
            failed.state = TaskState.FAILED
            failed.metadata["error"] = "malformed_task: no message"
            self._tasks[task.task_id] = failed
            await record_audit(self._runtime, failed, result="failure",
                               error="malformed_task")
            return failed
        if task.deadline is not None and task.deadline <= utc_now():
            failed = task.model_copy(deep=True)
            failed.state = TaskState.FAILED
            failed.metadata["error"] = "deadline_exceeded"
            self._tasks[task.task_id] = failed
            await record_audit(self._runtime, failed, result="failure",
                               error="deadline_exceeded")
            return failed

        # 3. Track inbound provenance regardless of security outcome.
        await track_input_provenance(self._runtime, task)

        # 4. Security filter.
        decision = await self._security.evaluate(task, token=token)
        if not decision.allowed:
            denied = task.model_copy(deep=True)
            if decision.policy_decision is not None and decision.approval_id:
                # REQUIRE_HITL: task stays SUBMITTED pending approval.
                denied.state = TaskState.SUBMITTED
                denied.metadata["pending_approval_id"] = decision.approval_id
            else:
                denied.state = TaskState.FAILED
            denied.metadata["security_reason"] = decision.reason
            if decision.approval_id:
                denied.metadata["approval_id"] = decision.approval_id
            self._tasks[task.task_id] = denied
            await record_audit(self._runtime, denied, result="denied",
                               error=decision.reason,
                               approval_id=decision.approval_id)
            return denied

        # 5-6. Translate → dispatch via HandoffManager.receive().
        request = self._adapter.task_to_handoff_request(task)
        try:
            response = await self._handoff_manager.receive(request, self._handler)
        except Exception as exc:  # noqa: BLE001
            response = HandoffResponse.failure(
                request.execution_id,
                errors=[f"a2a.server.dispatch_error: {exc}"],
                responder=request.target,
            )

        # 7. Translate back to A2ATask.
        updated = self._adapter.handoff_response_to_task(response, task)
        self._tasks[task.task_id] = updated

        # 8. Audit + evidence + output provenance.
        result_str = "success" if updated.state == TaskState.COMPLETED else "failure"
        first_error = (
            updated.metadata.get("errors", [None])[0]
            if updated.metadata.get("errors")
            else None
        )
        await record_audit(self._runtime, updated, result=result_str,
                           error=first_error)
        await register_evidence(self._runtime, updated, response)
        await track_output_provenance(self._runtime, updated, response)
        return updated

    async def get_status(self, task_id: str) -> A2ATaskStatus | None:
        """Return a status snapshot for a previously-submitted task."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return A2ATaskStatus(
            task_id=task.task_id,
            state=task.state,
            message=task.message,
            artifacts=list(task.artifacts),
            updated_at=utc_now(),
        )

    async def cancel(self, task_id: str) -> A2ATask | None:
        """Cancel a previously-submitted task.

        Returns the updated task (state=CANCELED) or None if not found.
        Already-terminal tasks are returned unchanged.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if task.is_terminal:
            return task
        task.state = TaskState.CANCELED
        task.metadata["canceled_at"] = utc_now().isoformat()
        await record_audit(self._runtime, task, result="success")
        return task

    # -----------------------------------------------------------------
    # Introspection
    # -----------------------------------------------------------------
    def list_tasks(self) -> list[A2ATask]:
        return list(self._tasks.values())

    def get_task(self, task_id: str) -> A2ATask | None:
        return self._tasks.get(task_id)


__all__ = ["A2AServer", "HandoffHandlerFn"]
