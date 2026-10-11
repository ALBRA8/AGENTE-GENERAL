"""ContractValidator — validates tool inputs/outputs against JSON Schemas.

Uses the `jsonschema` library if installed, otherwise falls back to a
small built-in validator that supports the subset of JSON Schema we use
(type, required, enum, properties, items, additionalProperties).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# =====================================================================
# Optional jsonschema dependency
# =====================================================================
try:
    import jsonschema  # type: ignore

    _HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    _HAS_JSONSCHEMA = False


# =====================================================================
# Manual fallback validator
# =====================================================================


def _manual_validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Tiny JSON-Schema validator supporting the subset we actually use."""
    errors: list[str] = []
    if not schema:
        return errors

    def visit(value: Any, subschema: dict[str, Any], path: str) -> None:
        if not isinstance(subschema, dict):
            return
        t = subschema.get("type")
        if t is not None:
            if not _matches_type(value, t):
                errors.append(f"{path}: expected type {t}, got {type(value).__name__}")
                return
        if "enum" in subschema and value not in subschema["enum"]:
            errors.append(f"{path}: value {value!r} not in enum {subschema['enum']}")
            return
        if t == "object" and isinstance(value, dict):
            required = subschema.get("required", [])
            for r in required:
                if r not in value:
                    errors.append(f"{path}: missing required property '{r}'")
            props = subschema.get("properties", {})
            for k, v in value.items():
                if k in props:
                    visit(v, props[k], f"{path}.{k}")
                elif subschema.get("additionalProperties") is False:
                    errors.append(f"{path}: additional property '{k}' not allowed")
        if t == "array" and isinstance(value, list):
            items = subschema.get("items")
            if items and isinstance(items, dict):
                for i, v in enumerate(value):
                    visit(v, items, f"{path}[{i}]")

    visit(instance, schema, "$")
    return errors


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
    if t == "null":
        return value is None
    return True


# =====================================================================
# Public API
# =====================================================================


@dataclass
class ValidationResult:
    """Outcome of a contract validation."""

    valid: bool
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # convenience truthiness
        return self.valid


class ContractValidator:
    """Validates payloads against a `ToolContract`'s input/output schemas."""

    def __init__(self, *, prefer_jsonschema: bool = True) -> None:
        self.prefer_jsonschema = prefer_jsonschema and _HAS_JSONSCHEMA

    # ------------------------------------------------------------------
    def validate_input(
        self,
        arguments: dict[str, Any],
        input_schema: dict[str, Any],
    ) -> ValidationResult:
        return self._validate(arguments, input_schema or {})

    def validate_output(
        self,
        output: Any,
        output_schema: dict[str, Any],
    ) -> ValidationResult:
        return self._validate(output, output_schema or {})

    # ------------------------------------------------------------------
    def _validate(self, instance: Any, schema: dict[str, Any]) -> ValidationResult:
        if not schema:
            return ValidationResult(valid=True)
        if self.prefer_jsonschema:
            try:
                jsonschema.validate(instance=instance, schema=schema)
                return ValidationResult(valid=True)
            except jsonschema.ValidationError as exc:  # type: ignore
                return ValidationResult(valid=False, errors=[exc.message])
            except Exception as exc:  # pragma: no cover
                # Fall back if jsonschema raises unexpectedly
                errs = _manual_validate(instance, schema)
                if errs:
                    return ValidationResult(valid=False, errors=errs + [f"jsonschema_error: {exc}"])
                return ValidationResult(valid=True)
        errs = _manual_validate(instance, schema)
        return ValidationResult(valid=errs == [], errors=errs)
