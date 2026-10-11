"""SkillContract — formal model extending `Skill` with validation rules.

A `Skill` (from core.base) describes a procedure declaratively. A
`SkillContract` adds:
  - explicit input/output JSON schemas
  - permission requirements (added to the base `permissions` list)
  - tests list (referenced by id)
  - acceptance predicates (callables or string descriptions)
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from albra_seed.core.base import Skill
from albra_seed.core.types import RiskLevel


class SkillContract(Skill):
    """Formal, validated skill contract.

    Skills registered in the SkillRegistry MUST be wrapped in a
    SkillContract so the SkillValidator can check inputs, outputs,
    permissions, and tests.
    """

    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: list[str] = Field(default_factory=list)
    max_duration_seconds: int = 120
    is_idempotent: bool = False

    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _validate_contract(self) -> "SkillContract":
        # Structural validation only — presence/value checks are delegated to
        # the SkillValidator so a malformed contract can be constructed and
        # then reported with proper error messages.
        if not isinstance(self.input_schema, dict):
            raise ValueError("input_schema must be a dict")
        if not isinstance(self.output_schema, dict):
            raise ValueError("output_schema must be a dict")
        if not isinstance(self.permissions, list):
            raise ValueError("permissions must be a list")
        if not isinstance(self.dependencies, list):
            raise ValueError("dependencies must be a list")
        if not isinstance(self.tests, list):
            raise ValueError("tests must be a list")
        if not isinstance(self.acceptance_criteria, list):
            raise ValueError("acceptance_criteria must be a list")
        return self

    # ------------------------------------------------------------------
    def requires_permissions(self) -> list[str]:
        return list(self.permissions)

    def requires_tools(self) -> list[str]:
        return list(self.dependencies)
