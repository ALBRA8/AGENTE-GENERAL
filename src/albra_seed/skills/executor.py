"""SkillExecutor — executes a registered skill by id.

Wraps execution with:
  - permission check (caller must possess all `permissions` the skill requires)
  - JSON-schema input validation (via SkillValidator.validate_input)
  - evidence-capture hook (optional async callback)
  - trace capture (appended to RunContext.outputs["skill_traces"])

Skills are procedural: they are executed by passing arguments to a
handler callable registered alongside the contract. The SEED itself
ships no skill handlers — they are registered by specialization layers.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from albra_seed.core.types import RunContext, ToolResult, new_id, utc_now
from albra_seed.skills.contract import SkillContract
from albra_seed.skills.lifecycle import SkillLifecycle, SkillState
from albra_seed.skills.validator import SkillValidator


# =====================================================================
# Types
# =====================================================================


SkillHandler = Callable[[dict[str, Any], RunContext | None], Awaitable[dict[str, Any]]]


@dataclass
class SkillExecutionTrace:
    skill_id: str
    skill_name: str
    started_at: str = ""
    finished_at: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    errors: list[str] = field(default_factory=list)
    permission_checked: bool = False
    input_validated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
            "errors": list(self.errors),
            "permission_checked": self.permission_checked,
            "input_validated": self.input_validated,
        }


SkillEvidenceSink = Callable[[ToolResult, SkillExecutionTrace, SkillContract, RunContext | None], Awaitable[None]]


class SkillNotActiveError(RuntimeError):
    pass


class SkillPermissionDeniedError(RuntimeError):
    pass


class SkillValidationError(RuntimeError):
    pass


class SkillTimeoutError(RuntimeError):
    pass


# =====================================================================
# SkillExecutor
# =====================================================================


class SkillExecutor:
    """Executes registered skills. The registry passes handler callables in."""

    def __init__(
        self,
        *,
        validator: SkillValidator | None = None,
        lifecycle: SkillLifecycle | None = None,
        evidence_sink: SkillEvidenceSink | None = None,
    ) -> None:
        self._validator = validator or SkillValidator()
        self._lifecycle = lifecycle or SkillLifecycle()
        self._evidence_sink = evidence_sink

    # ------------------------------------------------------------------
    async def execute(
        self,
        contract: SkillContract,
        handler: SkillHandler,
        arguments: dict[str, Any],
        *,
        caller_permissions: set[str] | None = None,
        context: RunContext | None = None,
    ) -> ToolResult:
        trace = SkillExecutionTrace(
            skill_id=contract.skill_id,
            skill_name=contract.name,
            started_at=utc_now().isoformat(),
        )
        t0 = time.perf_counter()

        # --- state check ---
        state = self._lifecycle.state(contract.skill_id)
        if state is not None and state != SkillState.ACTIVE:
            err = f"skill '{contract.name}' is not active (state={state.value if state else None})"
            trace.errors.append(err)
            return ToolResult(
                call_id=new_id("skillcall"),
                success=False,
                error=err,
                output={"_trace": trace.to_dict()},
            )

        # --- permission check ---
        caller_permissions = set(caller_permissions or [])
        required_permissions = set(contract.permissions)
        if required_permissions and not required_permissions.issubset(caller_permissions):
            missing = sorted(required_permissions - caller_permissions)
            err = f"permission denied: missing {missing}"
            trace.errors.append(err)
            return ToolResult(
                call_id=new_id("skillcall"),
                success=False,
                error=err,
                output={"_trace": trace.to_dict(), "missing_permissions": missing},
            )
        trace.permission_checked = True

        # --- input validation ---
        vres = self._validator.validate_input(contract, arguments)
        if not vres.valid:
            err = f"invalid input: {vres.errors}"
            trace.errors.append(err)
            return ToolResult(
                call_id=new_id("skillcall"),
                success=False,
                error=err,
                output={"_trace": trace.to_dict()},
            )
        trace.input_validated = True

        # --- execute with timeout ---
        try:
            output = await asyncio.wait_for(
                handler(arguments, context),
                timeout=contract.max_duration_seconds,
            )
        except asyncio.TimeoutError:
            trace.timed_out = True
            trace.errors.append(f"timeout after {contract.max_duration_seconds}s")
            result = ToolResult(
                call_id=new_id("skillcall"),
                success=False,
                error=f"timeout after {contract.max_duration_seconds}s",
                output={"_trace": trace.to_dict()},
            )
            self._finalize(trace, result, t0, context, contract)
            return result
        except Exception as exc:
            trace.errors.append(f"handler_error: {exc}")
            result = ToolResult(
                call_id=new_id("skillcall"),
                success=False,
                error=str(exc),
                output={"_trace": trace.to_dict()},
            )
            self._finalize(trace, result, t0, context, contract)
            return result

        # --- success ---
        result = ToolResult(
            call_id=new_id("skillcall"),
            success=True,
            output=output,
        )
        self._finalize(trace, result, t0, context, contract)
        return result

    # ------------------------------------------------------------------
    def _finalize(
        self,
        trace: SkillExecutionTrace,
        result: ToolResult,
        t0: float,
        context: RunContext | None,
        contract: SkillContract,
    ) -> None:
        trace.finished_at = utc_now().isoformat()
        trace.duration_ms = (time.perf_counter() - t0) * 1000
        # Embed trace into output (ToolResult has no metadata field).
        existing = result.output
        if isinstance(existing, dict):
            merged = {**existing, "_trace": trace.to_dict()}
        else:
            merged = {"value": existing, "_trace": trace.to_dict()}
        result.output = merged  # type: ignore[assignment]
        result.duration_ms = trace.duration_ms

        if context is not None:
            context.outputs.setdefault("skill_traces", []).append(trace.to_dict())

        if self._evidence_sink is not None and contract.evidence_required and result.success:
            try:
                asyncio.get_event_loop().create_task(
                    self._evidence_sink(result, trace, contract, context)
                )
            except Exception:
                pass
