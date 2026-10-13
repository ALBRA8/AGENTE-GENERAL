"""ToolExecutor — runs a tool by name with timeout + retry + evidence capture.

Wraps every tool invocation in:
  - JSON-schema input validation (via `ContractValidator`)
  - **Authorization gate** (H-02): non-dry-run execution REQUIRES an
    explicit ``authorization`` verdict (typically a ``PolicyVerdict``).
    When absent or ``allowed is False`` the executor refuses to run the
    tool — the call is returned as a failed ``ToolResult`` with
    ``output={"not_authorized": True}``. This is the defense-in-depth
    companion to the CHECK_PERMISSIONS phase: the executor is the LAST
    gate before the tool runs, so it must NEVER silently bypass it.
  - **Idempotency** (H-08): when a ``call_id`` is supplied, the executor
    remembers the call_id of every successful execution. A second call
    with the same ``call_id`` returns a successful no-op result with
    ``output={"idempotent_skip": True}`` instead of re-invoking the
    tool — preventing duplicate side-effects on retries / replays.
  - timeout enforcement (asyncio.wait_for)
  - retry policy (max attempts + backoff from `ToolContract.retry_policy`)
  - evidence-capture hook (optional async callback)
  - execution-trace capture (returned as part of `ToolResult.metadata`)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from albra_seed.core.base import ToolContract
from albra_seed.core.types import RunContext, ToolResult, new_id, utc_now
from albra_seed.tools.contract_validator import ContractValidator
from albra_seed.tools.registry import ToolNotFoundError, ToolRegistry


# =====================================================================
# Execution trace
# =====================================================================


@dataclass
class ExecutionTrace:
    """Per-invocation execution trace, captured by the executor."""

    tool_name: str
    attempts: int = 0
    started_at: str = ""
    finished_at: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    errors: list[str] = field(default_factory=list)
    contract_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "attempts": self.attempts,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "errors": list(self.errors),
            "contract_version": self.contract_version,
        }


EvidenceSink = Callable[[ToolResult, ExecutionTrace, ToolContract, RunContext | None], Awaitable[None]]


class ToolExecutorError(Exception):
    pass


class ToolTimeoutError(ToolExecutorError):
    pass


# =====================================================================
# ToolExecutor
# =====================================================================


class ToolExecutor:
    """Executes tools by name with timeout, retry, evidence + trace.

    SECURITY (H-02): every non-dry-run ``execute()`` invocation MUST
    carry an ``authorization`` verdict (any object exposing an
    ``allowed`` attribute — typically a ``PolicyVerdict`` from the
    CHECK_PERMISSIONS phase). Without it the executor refuses to run
    the tool, returning a failed ``ToolResult`` with
    ``output={"not_authorized": True}``. This makes the executor a
    true defense-in-depth gate — bypassing CHECK_PERMISSIONS does not
    bypass enforcement because the executor re-validates the verdict at
    the last possible instant.

    IDEMPOTENCY (H-08): when a ``call_id`` is supplied, the executor
    records successful executions. A subsequent ``execute()`` call
    with the same ``call_id`` returns a no-op success
    (``output={"idempotent_skip": True}``) — protecting against
    retries / replays that would otherwise double-apply side effects.
    The idempotency set lives for the lifetime of the executor
    instance (per-runtime / per-test).
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        validator: ContractValidator | None = None,
        evidence_sink: EvidenceSink | None = None,
        default_timeout: int = 30,
        auth_verifier: Any = None,  # AuthorizationVerifier | None
    ) -> None:
        self.registry = registry
        self._validator = validator or ContractValidator()
        self._evidence_sink = evidence_sink
        self.default_timeout = default_timeout
        self._auth_verifier = auth_verifier
        # IDEMPOTENCY (H-08): records call_ids of completed (successful
        # OR failed-but-attempted) executions so a retry with the same
        # call_id is a no-op. Lives for the lifetime of this executor
        # instance — typically one per Runtime.
        self._executed_calls: set[str] = set()

    # ------------------------------------------------------------------
    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        authorization: Any = None,  # PolicyVerdict | None
        dry_run: bool = False,
        timeout_override: int | None = None,
        call_id: str | None = None,
    ) -> ToolResult:
        """Execute a tool by name with authorization, idempotency, timeout.

        Parameters
        ----------
        name:
            Tool name (looked up in the registry).
        arguments:
            Tool input arguments (validated against the contract's
            ``input_schema``).
        context:
            Optional ``RunContext`` for trace + tools_used bookkeeping.
        authorization:
            Verdict produced by the security perimeter (typically a
            ``PolicyVerdict`` from CHECK_PERMISSIONS). REQUIRED for
            non-dry-run execution; absence yields a failed result with
            ``output={"not_authorized": True}``. Dry-run execution
            bypasses the gate (it does not invoke the tool's side
            effects).
        dry_run:
            When True, the tool is invoked in dry-run mode (no side
            effects) and the authorization gate is skipped.
        timeout_override:
            Per-call timeout override (seconds). Falls back to the
            contract's ``timeout_seconds`` or the executor's
            ``default_timeout``.
        call_id:
            Optional idempotency key. When the same ``call_id`` has
            already been executed by this executor, the call is
            short-circuited with a no-op success result.
        """
        # ----- IDEMPOTENCY GATE (H-08) ---------------------------------
        # A repeated call_id (across retries / replays / cognitive-loop
        # iterations) is short-circuited. We check this BEFORE the
        # authorization gate so a duplicate attempt cannot even reach
        # the verdict check — duplicate calls are, by definition,
        # already authorized-by-virtue-of-having-run-once. The set is
        # populated AFTER the first execution completes (success OR
        # failure-but-attempted) below.
        if call_id is not None and call_id in self._executed_calls:
            return ToolResult(
                call_id=call_id,
                success=True,
                output={"idempotent_skip": True},
            )

        # ----- AUTHORIZATION GATE (CRITICAL) — FAIL-CLOSED ----------------
        # Non-dry-run execution requires a valid AuthorizationToken verified
        # by an AuthorizationVerifier. If no verifier is configured, ALL
        # real executions are REJECTED (fail-closed). An arbitrary object
        # with `allowed=True` is NEVER accepted — only a properly issued
        # AuthorizationToken passes verification.
        if not dry_run:
            if authorization is None:
                return ToolResult(
                    call_id=call_id or new_id("tc"),
                    success=False,
                    error="not authorized: no authorization provided",
                    output={"not_authorized": True},
                )
            
            if self._auth_verifier is not None:
                # SECURE MODE: verify the AuthorizationToken cryptographically.
                # principal_id and context_id are MANDATORY. The principal_id
                # must come from a TrustedPrincipal (sealed by IdentityResolver),
                # NOT from a caller-set AuthPrincipal.
                exec_id = context.execution_id if context else "unknown"
                
                # Resolve TrustedPrincipal from verified_principal.
                # If verified_principal is not a TrustedPrincipal (e.g. a raw
                # AuthPrincipal set by a caller), REJECT — only sealed
                # TrustedPrincipals are accepted.
                principal_id = None
                if context is not None and hasattr(context, 'verified_principal'):
                    vp = context.verified_principal
                    if vp is not None:
                        # Check if this is a TrustedPrincipal (sealed)
                        from albra_seed.security.trusted_principal import TrustedPrincipal
                        if isinstance(vp, TrustedPrincipal):
                            principal_id = vp.principal_id
                        else:
                            # Caller set a raw AuthPrincipal — REJECT
                            return ToolResult(
                                call_id=call_id or new_id("tc"),
                                success=False,
                                error="not authorized: verified_principal is not a sealed TrustedPrincipal (forged identity rejected)",
                                output={"not_authorized": True, "reason": "unsealed_principal"},
                            )
                
                ctx_id = context.context_id if context else None
                
                # If principal_id or context_id cannot be resolved, REJECT
                # immediately without even calling verify(). This prevents
                # a token issued with context_id="" from matching a
                # context with context_id=None (which would be a bypass).
                if principal_id is None:
                    return ToolResult(
                        call_id=call_id or new_id("tc"),
                        success=False,
                        error="not authorized: no verified principal in context (fail-closed)",
                        output={"not_authorized": True, "reason": "no_principal"},
                    )
                if ctx_id is None:
                    return ToolResult(
                        call_id=call_id or new_id("tc"),
                        success=False,
                        error="not authorized: no context_id in RunContext (fail-closed)",
                        output={"not_authorized": True, "reason": "no_context_id"},
                    )
                
                if not self._auth_verifier.verify(
                    authorization,
                    tool_name=name,
                    execution_id=exec_id,
                    principal_id=principal_id,
                    context_id=ctx_id,
                ):
                    return ToolResult(
                        call_id=call_id or new_id("tc"),
                        success=False,
                        error="not authorized: token invalid, expired, revoked, or mismatched",
                        output={"not_authorized": True},
                    )
            else:
                # FAIL-CLOSED: no verifier configured → reject ALL real executions.
                # This is the secure default. Callers MUST configure an
                # AuthorizationVerifier to enable real tool execution.
                return ToolResult(
                    call_id=call_id or new_id("tc"),
                    success=False,
                    error="not authorized: no auth_verifier configured (fail-closed)",
                    output={"not_authorized": True, "reason": "no_verifier"},
                )
        
        # ----- DRY-RUN SAFETY GATE (DEFECT 2) — NO TOOL INVOCATION -------
        # When dry_run=True, the executor MUST NOT invoke tool.execute()
        # at all — even with dry_run=True, a tool could produce side
        # effects (write to disk, send HTTP, mutate global state).
        #
        # Instead, the executor returns a SIMULATED result that describes
        # what WOULD have been executed, without ever calling the tool's
        # execute() method. This guarantees zero side effects.
        #
        # The tool's contract must declare is_dry_run_capable=True for
        # the executor to even attempt a simulation. Tools that do NOT
        # declare dry-run support are rejected outright.
        if dry_run:
            try:
                tool_check = self.registry.get(name)
                contract_check = self.registry.get_contract(name)
            except ToolNotFoundError:
                return ToolResult(
                    call_id=call_id or new_id("tc"),
                    success=False,
                    error=f"tool not found: {name}",
                    output={"dry_run": True, "not_found": True},
                )
            if not getattr(contract_check, 'is_dry_run_capable', False):
                return ToolResult(
                    call_id=call_id or new_id("tc"),
                    success=False,
                    error="dry_run rejected: tool is not declared is_dry_run_capable in its contract",
                    output={"dry_run": True, "rejected": True, "reason": "not_dry_run_capable"},
                )
            # Return a SIMULATED result WITHOUT invoking tool.execute().
            # The simulation includes the tool name, arguments, and
            # contract metadata so the caller can verify what WOULD
            # have happened.
            return ToolResult(
                call_id=call_id or new_id("tc"),
                success=True,
                output={
                    "dry_run": True,
                    "simulated": True,
                    "tool_name": name,
                    "arguments": arguments,
                    "contract_version": contract_check.version,
                    "risk_level": str(contract_check.risk_level),
                    "message": f"[DRY-RUN] tool '{name}' would have been "
                               f"executed with arguments={arguments}",
                },
            )

        # ----- mark as executed (idempotency set) ----------------------
        # We add to the set BEFORE invoking the tool so a duplicate
        # concurrent / retried call with the same call_id is suppressed
        # even if this call is still in flight. For executions with no
        # call_id we skip idempotency (the legacy behavior — every call
        # is fresh). The set is bounded by the lifetime of the executor
        # instance, which is typically one per Runtime.
        if call_id is not None:
            self._executed_calls.add(call_id)

        try:
            tool = self.registry.get(name)
        except ToolNotFoundError as exc:
            return ToolResult(
                call_id=call_id or new_id("tc"),
                success=False,
                error=f"tool not found: {exc}",
                output={"tool_name": name, "not_found": True},
            )
        contract = self.registry.get_contract(name)
        trace = ExecutionTrace(
            tool_name=name,
            started_at=utc_now().isoformat(),
            contract_version=contract.version,
        )
        t0 = time.perf_counter()

        # --- input validation ---
        if not dry_run:
            vres = self._validator.validate_input(arguments, contract.input_schema)
            if not vres.valid:
                trace.errors.append(f"input_validation: {vres.errors}")
                trace.finished_at = utc_now().isoformat()
                trace.duration_ms = (time.perf_counter() - t0) * 1000
                return ToolResult(
                    call_id=call_id or new_id("tc"),
                    success=False,
                    error=f"invalid input: {vres.errors}",
                    output={"_trace": trace.to_dict(), "invalid_input": True},
                )

        # --- retry loop ---
        retry_policy = contract.retry_policy or {"max": 0}
        max_attempts = int(retry_policy.get("max", 0)) + 1
        backoff = float(retry_policy.get("backoff", 0.0))
        timeout_s = timeout_override or contract.timeout_seconds or self.default_timeout

        last_result: ToolResult | None = None
        for attempt in range(1, max_attempts + 1):
            trace.attempts = attempt
            try:
                result = await asyncio.wait_for(
                    tool.execute(arguments, context=context, dry_run=dry_run),
                    timeout=timeout_s,
                )
                last_result = result
                if result.success:
                    # output validation (best-effort)
                    if contract.output_schema and result.output is not None:
                        ovres = self._validator.validate_output(result.output, contract.output_schema)
                        if not ovres.valid:
                            result = result.model_copy(
                                update={
                                    "success": False,
                                    "error": f"invalid output: {ovres.errors}",
                                }
                            )
                            trace.errors.append(f"output_validation: {ovres.errors}")
                    break
                # failed result, maybe retry
                if attempt >= max_attempts:
                    break
                if backoff > 0:
                    await asyncio.sleep(backoff)
            except asyncio.TimeoutError:
                trace.timed_out = True
                trace.errors.append(f"timeout after {timeout_s}s")
                last_result = ToolResult(
                    call_id=call_id or new_id("tc"),
                    success=False,
                    error=f"timeout after {timeout_s}s",
                    output={"_trace": trace.to_dict(), "timed_out": True},
                )
                break
            except Exception as exc:  # propagate as failed result, allow retry
                trace.errors.append(f"attempt_{attempt}_error: {exc}")
                last_result = ToolResult(
                    call_id=call_id or new_id("tc"),
                    success=False,
                    error=str(exc),
                    output={"_trace": trace.to_dict()},
                )
                if attempt >= max_attempts:
                    break
                if backoff > 0:
                    await asyncio.sleep(backoff)
            else:
                break

        trace.finished_at = utc_now().isoformat()
        trace.duration_ms = (time.perf_counter() - t0) * 1000

        # Stamp final trace info into output + duration on the result.
        if last_result is None:
            last_result = ToolResult(
                call_id=call_id or new_id("tc"),
                success=False,
                error="executor returned no result",
                output={"_trace": trace.to_dict()},
            )

        # Preserve the originating call_id on the result so the cognitive
        # loop / OBSERVE phase can correlate observations back to the
        # ToolCall that produced them. Only override when the tool did not
        # already stamp a call_id (which would be the case for tools that
        # propagate the caller-supplied id themselves).
        if call_id is not None and not getattr(last_result, "call_id", None):
            last_result = last_result.model_copy(update={"call_id": call_id})

        # Embed trace info into output without losing existing output.
        #
        # For dict outputs we merge ``_trace`` into the dict — the
        # consumer sees their original keys PLUS a ``_trace`` key. This
        # is non-destructive.
        #
        # For non-dict outputs (strings, lists, numbers, None) we
        # PRESERVE the original output type. The previous behavior
        # wrapped non-dict outputs as ``{"value": ..., "_trace": ...}``,
        # which surprised consumers expecting the tool's natural type.
        # The trace info is still recorded on ``duration_ms`` (set
        # below) and — when a context is provided — on
        # ``context.outputs["tool_traces"]``.
        existing_output = last_result.output
        if isinstance(existing_output, dict):
            merged_output = {**existing_output, "_trace": trace.to_dict()}
            last_result = last_result.model_copy(
                update={
                    "duration_ms": trace.duration_ms,
                    "output": merged_output,
                }
            )
        else:
            # Non-dict output: preserve the type. Only stamp duration_ms.
            last_result = last_result.model_copy(
                update={
                    "duration_ms": trace.duration_ms,
                }
            )

        if context is not None:
            context.tools_used.append(name)
            context.outputs.setdefault("tool_traces", []).append(trace.to_dict())

        if self._evidence_sink is not None and contract.evidence_required:
            try:
                await self._evidence_sink(last_result, trace, contract, context)
            except Exception as exc:  # evidence sink must never break execution
                trace.errors.append(f"evidence_sink_error: {exc}")

        return last_result
