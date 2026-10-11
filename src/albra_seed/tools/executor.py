"""ToolExecutor — runs a tool by name with timeout + retry + evidence capture.

Wraps every tool invocation in:
  - JSON-schema input validation (via `ContractValidator`)
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
    """Executes tools by name with timeout, retry, evidence + trace."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        validator: ContractValidator | None = None,
        evidence_sink: EvidenceSink | None = None,
        default_timeout: int = 30,
    ) -> None:
        self.registry = registry
        self._validator = validator or ContractValidator()
        self._evidence_sink = evidence_sink
        self.default_timeout = default_timeout

    # ------------------------------------------------------------------
    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
        timeout_override: int | None = None,
    ) -> ToolResult:
        try:
            tool = self.registry.get(name)
        except ToolNotFoundError as exc:
            return ToolResult(
                call_id=new_id("tc"),
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
                    call_id=new_id("tc"),
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
                    call_id=new_id("tc"),
                    success=False,
                    error=f"timeout after {timeout_s}s",
                    output={"_trace": trace.to_dict(), "timed_out": True},
                )
                break
            except Exception as exc:  # propagate as failed result, allow retry
                trace.errors.append(f"attempt_{attempt}_error: {exc}")
                last_result = ToolResult(
                    call_id=new_id("tc"),
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
                call_id=new_id("tc"),
                success=False,
                error="executor returned no result",
                output={"_trace": trace.to_dict()},
            )

        # Embed trace info into output without losing existing output.
        existing_output = last_result.output
        if isinstance(existing_output, dict):
            merged_output = {**existing_output, "_trace": trace.to_dict()}
        else:
            merged_output = {"value": existing_output, "_trace": trace.to_dict()}
        last_result = last_result.model_copy(
            update={
                "duration_ms": trace.duration_ms,
                "output": merged_output,
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
