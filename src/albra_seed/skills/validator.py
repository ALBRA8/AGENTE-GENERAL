"""SkillValidator — validates skill contracts before registration.

Validates:
  - input/output JSON schemas shape
  - permissions declared
  - tests list referenced
  - dependencies (tool ids) present
  - acceptance_criteria present (warning if missing)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from albra_seed.skills.contract import SkillContract


# =====================================================================
# Result
# =====================================================================


@dataclass
class SkillValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.valid


# =====================================================================
# SkillValidator
# =====================================================================


class SkillValidator:
    """Validates a SkillContract: inputs/outputs/permissions/tests/dependencies."""

    def __init__(self, *, known_tool_ids: set[str] | None = None) -> None:
        self.known_tool_ids = set(known_tool_ids or [])

    # ------------------------------------------------------------------
    def set_known_tool_ids(self, tool_ids: set[str]) -> None:
        self.known_tool_ids = set(tool_ids)

    def validate(self, contract: SkillContract) -> SkillValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        # --- name ---
        if not contract.name:
            errors.append("name is required")
        if not contract.objective:
            errors.append("objective is required")
        if not contract.version:
            errors.append("version is required")

        # --- input schema ---
        if not isinstance(contract.input_schema, dict):
            errors.append("input_schema must be a dict")
        elif contract.input_schema:
            if contract.input_schema.get("type") not in (None, "object"):
                errors.append("input_schema.type must be 'object' if specified")

        # --- output schema ---
        if not isinstance(contract.output_schema, dict):
            errors.append("output_schema must be a dict")
        elif contract.output_schema:
            if contract.output_schema.get("type") not in (None, "object"):
                errors.append("output_schema.type must be 'object' if specified")

        # --- permissions ---
        if contract.risk_level.value in ("high", "critical") and not contract.permissions:
            errors.append(
                f"high/critical risk skills must declare permissions (risk={contract.risk_level.value})"
            )

        # --- dependencies / tools ---
        if self.known_tool_ids:
            missing = [d for d in contract.dependencies if d not in self.known_tool_ids]
            if missing:
                errors.append(f"unknown tool dependencies: {missing}")

        # --- tests ---
        if not contract.tests:
            warnings.append("skill has no tests declared")

        # --- acceptance criteria ---
        if not contract.acceptance_criteria:
            warnings.append("skill has no acceptance_criteria declared")

        # --- idempotency ---
        if contract.is_idempotent and contract.max_duration_seconds <= 0:
            errors.append("idempotent skill must have max_duration_seconds > 0")

        return SkillValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
        )

    def validate_input(self, contract: SkillContract, arguments: dict[str, Any]) -> SkillValidationResult:
        """Runtime validation: check provided arguments against input_schema."""
        errors: list[str] = []
        schema = contract.input_schema or {}
        if schema:
            required = schema.get("required", [])
            for r in required:
                if r not in arguments:
                    errors.append(f"missing required input '{r}'")
            props = schema.get("properties", {})
            for k, v in arguments.items():
                if k in props:
                    expected = props[k].get("type")
                    if expected and not _matches_type(v, expected):
                        errors.append(f"input '{k}' expected type {expected}")
        return SkillValidationResult(valid=not errors, errors=errors)


def _matches_type(value: Any, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    return True
