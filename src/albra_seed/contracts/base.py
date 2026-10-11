"""Structured output contracts — base class.

A `Contract` declares a name, version, and JSON schemas for input and
output. Contracts are the mechanism by which the SEED enforces
structured, validated exchanges between subsystems. Specialization
layers register domain-specific contracts; the SEED itself ships none.

A contract is *declarative* — it does not perform IO. Validation is
done by `ContractRegistry.validate()` using jsonschema-style checks
(or duck-typed field presence when jsonschema is unavailable).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now


class Contract(BaseModel):
    """Base structured-output contract.

    Attributes
    ----------
    contract_id:
        Unique identifier (auto-generated).
    name:
        Human-readable name. MUST be unique within a ContractRegistry.
    version:
        Semver-style version. Default "1.0.0".
    description:
        What the contract guarantees about the exchange.
    input_schema:
        JSON-Schema dict describing the expected input shape. Empty
        dict means "any input".
    output_schema:
        JSON-Schema dict describing the expected output shape.
    required_fields:
        Convenience list of output field names that MUST be present.
        Used as a fast duck-typed check when jsonschema is unavailable.
    metadata:
        Free-form extension bag.
    created_at:
        When this contract was first registered.
    """

    contract_id: str = Field(default_factory=lambda: new_id("contract"))
    name: str
    version: str = "1.0.0"
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: Any = Field(default_factory=utc_now)

    # ------------------------------------------------------------------ #
    # Validation                                                         #
    # ------------------------------------------------------------------ #

    def validate_input(self, data: Any) -> list[str]:
        """Return a list of validation errors. Empty list = valid."""
        return self._validate_against_schema(data, self.input_schema, "input")

    def validate_output(self, data: Any) -> list[str]:
        """Return a list of validation errors. Empty list = valid."""
        errors: list[str] = []
        # Fast path: required_fields presence check.
        for field in self.required_fields:
            if not isinstance(data, dict) or field not in data:
                errors.append(f"output missing required field: {field}")
        errors.extend(
            self._validate_against_schema(data, self.output_schema, "output")
        )
        return errors

    def is_valid_input(self, data: Any) -> bool:
        return not self.validate_input(data)

    def is_valid_output(self, data: Any) -> bool:
        return not self.validate_output(data)

    # ------------------------------------------------------------------ #
    # Snapshot                                                           #
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "required_fields": list(self.required_fields),
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
        }

    # ------------------------------------------------------------------ #
    # Internal                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_against_schema(
        data: Any, schema: dict[str, Any], label: str
    ) -> list[str]:
        """Validate `data` against `schema`. Returns list of error strings.

        Tries jsonschema first (if installed); falls back to a simple
        duck-typed `required` field check.
        """
        if not schema:
            return []
        try:
            import jsonschema  # type: ignore

            try:
                jsonschema.validate(instance=data, schema=schema)
                return []
            except jsonschema.ValidationError as exc:
                return [f"{label}: {exc.message}"]
        except ImportError:
            # Fallback: only honour `required` (a list of property names).
            required = schema.get("required", [])
            if not required:
                return []
            if not isinstance(data, dict):
                return [f"{label}: expected dict, got {type(data).__name__}"]
            missing = [r for r in required if r not in data]
            return [f"{label}: missing required: {r}" for r in missing]


__all__ = ["Contract"]
