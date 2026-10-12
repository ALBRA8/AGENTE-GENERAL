"""The ALBRA Agent.

The Agent is the user-facing entry point. It implements the universal
ALBRA agent loop:

    RECEIVE → UNDERSTAND → REASON → PLAN → CHECK_PERMISSIONS →
    EXECUTE → OBSERVE → VALIDATE → RECORD → RESPOND

Each phase is a stub that delegates to a Runtime-registered component.
If a component is missing, the phase is SKIPPED (and recorded in the
trace) rather than crashing — this keeps CORE_ONLY mode functional.

The Agent is stateless between runs: all execution state lives on the
RunContext (reconstructable) and the Session (durable). The Agent holds
only identity + a reference to the Runtime.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog

from albra_seed.core.base import LLMProvider
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import (
    AgentResponse,
    AutonomyLevel,
    Message,
    Role,
    RunContext,
    Session,
    ToolCall,
    ToolResult,
    utc_now,
)
from albra_seed.runtime.loop import (
    AgentLoop,
    PhaseEvent,
    PhaseFn,
    PhaseStatus,
)
from albra_seed.runtime.runtime import (
    COMPONENT_APPROVAL_ENGINE,
    COMPONENT_AUDIT_LOGGER,
    COMPONENT_EVIDENCE_COLLECTOR,
    COMPONENT_FIREWALL,
    COMPONENT_POLICY_ENGINE,
    COMPONENT_PROVIDER_ROUTER,
    Runtime,
)
from albra_seed.runtime.state import AgentState

if TYPE_CHECKING:  # pragma: no cover
    # Imported only for type hints; concrete classes are provided by other
    # subagents at runtime via the registry.
    from albra_seed.approvals.engine import ApprovalEngine  # noqa: F401
    from albra_seed.policy.engine import PolicyEngine  # noqa: F401
    from albra_seed.providers.router import ProviderRouter  # noqa: F401
    from albra_seed.security.firewall import ActionFirewall  # noqa: F401

log = structlog.get_logger("albra.agent")


class Agent:
    """A single ALBRA agent instance bound to a Runtime.

    The Agent is intentionally domain-agnostic. Specialization layers
    subclass Agent (or compose one) and register domain-specific tools,
    skills, providers, and policies with the Runtime.
    """

    def __init__(self, identity: AgentIdentity, runtime: Runtime) -> None:
        self.identity = identity
        self.runtime = runtime
        self.state = AgentState(agent_id=identity.agent_id)
        runtime.register_agent_state(self.state)
        self._cancel_events: dict[str, asyncio.Event] = {}

    # =====================================================================
    # PUBLIC API
    # =====================================================================

    async def run(
        self,
        input: str | Message,
        *,
        session: Session | None = None,
        context: RunContext | None = None,
        timeout_seconds: float | None = None,
    ) -> AgentResponse:
        """Execute the universal agent loop and return an AgentResponse.

        Parameters
        ----------
        input : str | Message
            The user input. Strings are wrapped into a USER Message.
        session : Session | None
            Optional conversation session. A new one is created if absent.
        context : RunContext | None
            Optional pre-built RunContext (e.g. for replay). A fresh one is
            constructed if absent.
        timeout_seconds : float | None
            Per-phase timeout override.
        """
        # 0. Bootstrap session + context.
        if session is None:
            session = Session(agent_id=self.identity.agent_id)
        await self.runtime.register_session(session)

        if context is None:
            context = RunContext(
                agent_id=self.identity.agent_id,
                session_id=session.session_id,
                task=input if isinstance(input, str) else input.content,
                autonomy_level=self.identity.autonomy_level,
                permissions=list(self.identity.permissions),
            )

        # Mark state.
        self.state.begin_execution(context)
        cancel_event = asyncio.Event()
        self._cancel_events[context.execution_id] = cancel_event

        # Receive the message.
        message = (
            input
            if isinstance(input, Message)
            else Message(role=Role.USER, content=input)
        )
        message.correlation_id = context.correlation_id
        session.messages.append(message)
        session.touch()

        # Build the loop and register Runtime-wide hooks.
        loop = AgentLoop(
            self, timeout_seconds=timeout_seconds or 60.0
        )
        for hook in self.runtime.phase_hooks:
            loop.add_hook(hook)

        ctx_dict: dict[str, Any] = {
            "execution_id": context.execution_id,
            "correlation_id": context.correlation_id,
            "session": session,
            "context": context,
            "input_message": message,
            "cancel_event": cancel_event,
            "identity": self.identity,
        }

        phase_fns = self._build_phase_fns()
        try:
            await loop.run(phase_fns, ctx_dict)
        except asyncio.CancelledError:
            context.status = "cancelled"
            context.errors.append("execution cancelled")
            raise
        finally:
            self._cancel_events.pop(context.execution_id, None)

        # Build final response from loop outputs.
        response = self._build_response(context, session, ctx_dict, loop.trace)

        # Update session + state.
        session.messages.append(
            Message(
                role=Role.ASSISTANT,
                content=response.content,
                correlation_id=context.correlation_id,
            )
        )
        session.touch()
        context.finished_at = utc_now()
        context.status = "succeeded" if not context.errors else "failed"

        if context.status == "succeeded":
            self.state.finish_execution(response)
        else:
            self.state.fail_execution(
                "; ".join(context.errors) or "execution failed"
            )
        return response

    def cancel(self, execution_id: str) -> None:
        """Request cancellation of a running execution."""
        ev = self._cancel_events.get(execution_id)
        if ev is not None:
            ev.set()

    # =====================================================================
    # PHASE BUILDERS
    # =====================================================================

    def _build_phase_fns(self) -> dict[str, PhaseFn]:
        """Map each canonical phase name to its implementation.

        Phase implementations are stubs: they look up the appropriate
        Runtime component and call it. Missing components are recorded
        as SKIPPED phases in the trace rather than raising.
        """
        return {
            "RECEIVE": self._phase_receive,
            "UNDERSTAND": self._phase_understand,
            "REASON": self._phase_reason,
            "PLAN": self._phase_plan,
            "CHECK_PERMISSIONS": self._phase_check_permissions,
            "EXECUTE": self._phase_execute,
            "OBSERVE": self._phase_observe,
            "VALIDATE": self._phase_validate,
            "RECORD": self._phase_record,
            "RESPOND": self._phase_respond,
        }

    # ---------------------------------------------------------------------
    # Each phase returns its output (stored in ctx_dict[phase_name]).
    # Each phase may set ctx_dict["abort"] = True to stop the loop.
    # ---------------------------------------------------------------------

    async def _phase_receive(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        """Receive the inbound message and normalize it."""
        message: Message = ctx["input_message"]
        run_ctx: RunContext = ctx["context"]
        run_ctx.inputs["message"] = message.content
        return {"received": True, "content": message.content, "role": message.role.value}

    async def _phase_understand(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        """Classify intent + extract structured inputs (stub)."""
        content: str = ctx["RECEIVE"]["content"]
        return {"intent": "default", "raw_content": content, "entities": {}}

    async def _phase_reason(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        """Call an LLM provider to produce a reasoning trace.

        Resolution order (since GAP-5):

        1. If the caller has explicitly registered providers via
           `runtime.register_provider(...)`, the FIRST one (preferring
           `identity.providers`) is called directly. This preserves the
           pre-GAP-5 behavior where a custom Mock/Stub provider would
           always be the one to handle the request.

        2. Otherwise, fall back to the `provider_router` component
           (auto-registered at `Runtime.startup()` in CORE_ONLY mode).
           The router routes through its own registry, terminating at
           the always-on Mock provider when nothing else is configured.

        This keeps CORE_ONLY birth working out of the box (router auto-
        registered with Mock) AND keeps existing tests / specializations
        that register a custom provider directly via `register_provider()`
        working unchanged.
        """
        messages = [ctx["input_message"]]
        provider = self._default_provider()
        if provider is not None:
            response = await provider.complete(messages, context=ctx["context"])
            ctx["draft_response"] = response
            return {"reasoning": "provider", "draft": response.content}
        router = self.runtime.get_component(COMPONENT_PROVIDER_ROUTER)
        if router is not None:
            # Provider router is responsible for routing + fallbacks.
            response = await router.complete(messages, context=ctx["context"])
            ctx["draft_response"] = response
            run_ctx: RunContext = ctx["context"]
            run_ctx.providers_used.append(getattr(router, "name", "router"))
            return {"reasoning": "router", "draft": response.content}
        # No provider and no router — emit a no-op draft so the rest of
        # the loop can proceed (CORE_ONLY graceful degradation).
        return {"reasoning": "no-provider", "draft": ctx["RECEIVE"]["content"]}

    async def _phase_plan(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        """Extract tool calls from the draft response (if any)."""
        draft: AgentResponse | None = ctx.get("draft_response")
        tool_calls: list[ToolCall] = list(draft.tool_calls) if draft else []
        return {"tool_calls": tool_calls, "plan": [tc.tool_name for tc in tool_calls]}

    async def _phase_check_permissions(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        """Run policy engine + firewall + (optionally) approval engine.

        SECURITY INVARIANT (DEFAULT-DENY):
        When a Tool call is in the plan, the absence of a security perimeter
        (firewall AND policy engine BOTH missing) MUST yield DENY — never ALLOW.
        CORE_ONLY without tools is unaffected (no plan -> no decision needed).
        """
        plan: list[ToolCall] = ctx["PLAN"]["tool_calls"]
        run_ctx: RunContext = ctx["context"]
        decisions: list[dict[str, Any]] = []

        firewall = self.runtime.get_component(COMPONENT_FIREWALL)
        policy = self.runtime.get_component(COMPONENT_POLICY_ENGINE)
        approval = self.runtime.get_component(COMPONENT_APPROVAL_ENGINE)

        for call in plan:
            # DEFAULT-DENY: a Tool call requires AT LEAST ONE active gate
            # (firewall OR policy engine). If BOTH are absent, DENY.
            # The decision only becomes "allow" if an active gate explicitly
            # allows it. CORE_ONLY without tools never reaches this loop.
            if firewall is None and policy is None:
                decision = "deny"
                decisions.append({
                    "tool": call.tool_name,
                    "decision": decision,
                    "reason": "no security perimeter configured (firewall + policy both absent)",
                })
                run_ctx.warnings.append(
                    f"tool call denied (no security perimeter): {call.tool_name}"
                )
                continue

            # At least one gate is active. Start from DENY; an active gate
            # must explicitly ALLOW (or REQUIRE_HITL) for the call to proceed.
            # Any active gate saying DENY wins (most restrictive).
            decision = "deny"
            require_hitl = False

            # Policy engine: explicit allow / deny / require_hitl.
            if policy is not None and hasattr(policy, "evaluate"):
                verdict = await policy.evaluate(call, context=run_ctx)
                v = getattr(verdict, "decision", None)
                if v == "deny":
                    decision = "deny"
                elif v == "require_hitl":
                    require_hitl = True
                elif v == "allow":
                    decision = "allow"
                # If policy returns unknown/None, decision stays "deny"

            # Firewall: can only upgrade deny→allow (when policy didn't deny)
            # or downgrade allow→deny (when firewall denies).
            if firewall is not None and hasattr(firewall, "check"):
                fw_decision = await firewall.check(call, context=run_ctx)
                if getattr(fw_decision, "allowed", True) is False:
                    decision = "deny"
                    require_hitl = False  # deny wins over hitl
                elif decision == "deny" and getattr(fw_decision, "allowed", False) is True:
                    # Firewall explicitly allows (policy was None or didn't say allow)
                    decision = "allow"

            if require_hitl:
                decision = "require_hitl"

            # Approval engine: HITL.
            if decision == "require_hitl" and approval is not None:
                approval_result = await approval.request(call, context=run_ctx)
                if getattr(approval_result, "status", None) != "approved":
                    decision = "deny"
                else:
                    decision = "allow"
                    run_ctx.approvals.append(
                        getattr(approval_result, "approval_id", "unknown")
                    )
            decisions.append({"tool": call.tool_name, "decision": decision})
            if decision == "deny":
                run_ctx.warnings.append(
                    f"tool call denied: {call.tool_name}"
                )

        # If any deny and autonomy below L2, abort execution.
        any_deny = any(d["decision"] == "deny" for d in decisions)
        if any_deny and self.identity.autonomy_level < AutonomyLevel.L2_EXECUTE_SAFE:
            ctx["abort"] = True
            run_ctx.errors.append("execution blocked by permission gate")
        return {"decisions": decisions, "aborted": ctx.get("abort", False)}

    async def _phase_execute(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        """Execute allowed tool calls. Honors cancellation."""
        cancel_event: asyncio.Event = ctx["cancel_event"]
        if cancel_event.is_set():
            ctx["abort"] = True
            return {"results": [], "cancelled": True}

        decisions = ctx["CHECK_PERMISSIONS"]["decisions"]
        plan: list[ToolCall] = ctx["PLAN"]["tool_calls"]
        run_ctx: RunContext = ctx["context"]
        results: list[ToolResult] = []

        for call, decision in zip(plan, decisions):
            if decision["decision"] != "allow":
                results.append(ToolResult(
                    call_id=call.id, success=False,
                    error=f"blocked: {decision['decision']}",
                ))
                continue
            tool = self.runtime.get_tool(call.tool_name)
            if tool is None:
                results.append(ToolResult(
                    call_id=call.id, success=False,
                    error=f"tool not found: {call.tool_name}",
                ))
                continue
            try:
                result = await tool.execute(call.arguments, context=run_ctx)
                results.append(result)
                run_ctx.tools_used.append(call.tool_name)
            except Exception as exc:  # noqa: BLE001
                results.append(ToolResult(
                    call_id=call.id, success=False,
                    error=f"{type(exc).__name__}: {exc}",
                ))
                run_ctx.errors.append(f"tool {call.tool_name} failed: {exc}")
        return {"results": results}

    async def _phase_observe(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        """Collect observations (tool outputs + provider outputs)."""
        results: list[ToolResult] = ctx.get("EXECUTE", {}).get("results", [])
        observations = [
            {"call_id": r.call_id, "success": r.success, "output": r.output}
            for r in results
        ]
        return {"observations": observations}

    async def _phase_validate(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        """Run output guardrails. Default deny on rejection."""
        draft: AgentResponse | None = ctx.get("draft_response")
        if draft is None:
            return {"valid": True, "guardrails_run": 0}
        run_ctx: RunContext = ctx["context"]
        ok = True
        run = 0
        for guardrail in self.runtime.guardrails:
            try:
                passed = await guardrail.check_output(draft, context=run_ctx)
                run += 1
                if not passed:
                    ok = False
                    run_ctx.warnings.append(
                        f"guardrail rejected output: {guardrail.name}"
                    )
            except Exception as exc:  # noqa: BLE001
                run_ctx.warnings.append(
                    f"guardrail {guardrail.name} raised: {exc}"
                )
        if not ok:
            run_ctx.errors.append("output rejected by guardrails")
        return {"valid": ok, "guardrails_run": run}

    async def _phase_record(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        """Persist execution to memory + audit log (best-effort)."""
        run_ctx: RunContext = ctx["context"]
        evidence_collector = self.runtime.get_component(COMPONENT_EVIDENCE_COLLECTOR)
        audit = self.runtime.get_component(COMPONENT_AUDIT_LOGGER)
        memory = self.runtime.get_memory_store("default")

        if evidence_collector is not None and hasattr(evidence_collector, "record"):
            try:
                ev = await evidence_collector.record(ctx)
                if ev is not None:
                    run_ctx.evidence_ids.append(
                        getattr(ev, "evidence_id", "unknown")
                    )
            except Exception as exc:  # noqa: BLE001
                run_ctx.warnings.append(f"evidence collector failed: {exc}")

        if audit is not None and hasattr(audit, "append"):
            try:
                await audit.append(run_ctx)
            except Exception as exc:  # noqa: BLE001
                run_ctx.warnings.append(f"audit log failed: {exc}")

        if memory is not None:
            try:
                from albra_seed.core.base import MemoryRecord  # noqa: PLC0415
                record = MemoryRecord(
                    agent_id=run_ctx.agent_id,
                    memory_type="working",
                    content=ctx.get("RECEIVE", {}).get("content"),
                    execution_id=run_ctx.execution_id,
                    correlation_id=run_ctx.correlation_id,
                )
                await memory.write(record)
            except Exception as exc:  # noqa: BLE001
                run_ctx.warnings.append(f"memory write failed: {exc}")

        return {"recorded": True}

    async def _phase_respond(self, agent: Agent, ctx: dict[str, Any]) -> dict[str, Any]:
        """Compose the final AgentResponse from the loop's outputs."""
        run_ctx: RunContext = ctx["context"]
        draft: AgentResponse | None = ctx.get("draft_response")
        results: list[ToolResult] = ctx.get("EXECUTE", {}).get("results", [])

        if draft is not None:
            content = draft.content
            confidence = draft.confidence
        else:
            content = ctx["RECEIVE"]["content"]
            confidence = 0.0
        return {
            "content": content,
            "confidence": confidence,
            "tool_results": results,
        }

    # =====================================================================
    # INTERNAL HELPERS
    # =====================================================================

    def _default_provider(self) -> LLMProvider | None:
        """Return the first registered provider, preferring identity.providers."""
        for name in self.identity.providers:
            provider = self.runtime.get_provider(name)
            if provider is not None:
                return provider
        # Fallback: any registered provider.
        if self.runtime._providers:  # noqa: SLF001
            return next(iter(self.runtime._providers.values()))  # noqa: SLF001
        return None

    def _build_response(
        self,
        context: RunContext,
        session: Session,
        ctx: dict[str, Any],
        trace: list[PhaseEvent],
    ) -> AgentResponse:
        """Assemble the final AgentResponse from loop outputs + trace."""
        respond_out = ctx.get("RESPOND", {}) or {}
        draft: AgentResponse | None = ctx.get("draft_response")
        results: list[ToolResult] = respond_out.get("tool_results", [])
        tool_calls: list[ToolCall] = ctx.get("PLAN", {}).get("tool_calls", [])

        content = respond_out.get("content") or (
            draft.content if draft else ""
        )
        confidence = float(respond_out.get("confidence", 0.0))

        warnings = list(context.warnings)
        errors = list(context.errors)
        if any(
            ev.status == PhaseStatus.FAILURE for ev in trace
        ):
            errors.append("one or more phases failed; see trace")

        # Build the execution trace on the RunContext (reconstructable).
        context.outputs["trace"] = [
            {
                "phase": e.phase,
                "status": e.status.value,
                "duration_ms": e.duration_ms,
                "error": e.error,
            }
            for e in trace
        ]

        return AgentResponse(
            content=content,
            role=Role.ASSISTANT,
            tool_calls=tool_calls,
            tool_results=results,
            execution_id=context.execution_id,
            correlation_id=context.correlation_id,
            confidence=confidence,
            evidence_ids=list(context.evidence_ids),
            warnings=warnings,
            errors=errors,
            metadata={
                "session_id": session.session_id,
                "agent_id": self.identity.agent_id,
                "tools_used": context.tools_used,
                "providers_used": context.providers_used,
                "approvals": context.approvals,
                "trace_len": len(trace),
            },
        )


__all__ = ["Agent"]
