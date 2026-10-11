"""DryRunExecutor — simulates tool execution without side effects.

For tools where `ToolContract.is_dry_run_capable == True`, the
`DryRunExecutor` invokes the tool with `dry_run=True` and stamps the
result `output["dry_run"] = True`. For non-dry-run-capable tools,
returns a synthetic success result without calling the tool at all.
"""

from __future__ import annotations

from typing import Any

from albra_seed.core.base import ToolContract
from albra_seed.core.types import RunContext, ToolResult, new_id
from albra_seed.tools.contract_validator import ContractValidator
from albra_seed.tools.registry import ToolNotFoundError, ToolRegistry


class DryRunExecutor:
    """Simulates tool execution without side effects."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        validator: ContractValidator | None = None,
    ) -> None:
        self.registry = registry
        self._validator = validator or ContractValidator()

    # ------------------------------------------------------------------
    async def simulate(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
    ) -> ToolResult:
        try:
            tool = self.registry.get(name)
        except ToolNotFoundError as exc:
            return ToolResult(
                call_id=new_id("tc"),
                success=False,
                error=f"tool not found: {exc}",
                output={"dry_run": True, "not_found": True, "tool_name": name},
            )

        contract: ToolContract = self.registry.get_contract(name)

        # Validate the input even in dry-run mode — bad inputs should fail fast.
        vres = self._validator.validate_input(arguments, contract.input_schema)
        if not vres.valid:
            return ToolResult(
                call_id=new_id("tc"),
                success=False,
                error=f"invalid input: {vres.errors}",
                output={"dry_run": True, "validated": False},
            )

        if contract.is_dry_run_capable:
            try:
                result = await tool.execute(arguments, context=context, dry_run=True)
            except Exception as exc:
                return ToolResult(
                    call_id=new_id("tc"),
                    success=False,
                    error=f"dry_run error: {exc}",
                    output={"dry_run": True, "error": str(exc)},
                )
            # Stamp dry_run info into output (without losing existing payload).
            existing = result.output
            if isinstance(existing, dict):
                merged = {**existing, "dry_run": True, "simulated": True}
            else:
                merged = {"value": existing, "dry_run": True, "simulated": True}
            return result.model_copy(update={"output": merged})

        # Non-dry-run-capable: synthesize a no-op result.
        return ToolResult(
            call_id=new_id("tc"),
            success=True,
            output={
                "simulated": True,
                "dry_run": True,
                "skipped_execution": True,
                "reason": "tool is not dry-run capable",
                "tool": name,
                "contract_version": contract.version,
            },
        )
