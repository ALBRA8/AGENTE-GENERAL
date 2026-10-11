"""SkillRegistry — register/validate/execute/version/disable/remove skills.

Skills are installed AFTER seed initialization without modifying core.
Each `Skill` (from core.base) is wrapped in a `SkillContract` for
validation, paired with a handler callable, and tracked by `SkillLifecycle`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from albra_seed.skills.contract import SkillContract
from albra_seed.skills.executor import (
    SkillExecutionTrace,
    SkillExecutor,
    SkillHandler,
    SkillNotActiveError,
)
from albra_seed.skills.lifecycle import SkillLifecycle, SkillState
from albra_seed.skills.validator import SkillValidationResult, SkillValidator


# =====================================================================
# Errors
# =====================================================================


class SkillAlreadyRegisteredError(KeyError):
    pass


class SkillNotFoundError(KeyError):
    pass


# =====================================================================
# Record
# =====================================================================


@dataclass
class SkillRecord:
    contract: SkillContract
    handler: SkillHandler
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


# =====================================================================
# SkillRegistry
# =====================================================================


class SkillRegistry:
    """Registers, validates, and executes skills. Each registration consists
    of (SkillContract, handler callable). Skills installed later are added
    without touching the core."""

    def __init__(
        self,
        *,
        validator: SkillValidator | None = None,
        executor: SkillExecutor | None = None,
        lifecycle: SkillLifecycle | None = None,
    ) -> None:
        self._skills: dict[str, SkillRecord] = {}
        self._validator = validator or SkillValidator()
        self._lifecycle = lifecycle or SkillLifecycle()
        self._executor = executor or SkillExecutor(
            validator=self._validator, lifecycle=self._lifecycle
        )

    # ------------------------------------------------------------------
    # Register / remove
    # ------------------------------------------------------------------
    def register(
        self,
        contract: SkillContract,
        handler: SkillHandler,
        *,
        metadata: dict[str, Any] | None = None,
        validate: bool = True,
    ) -> SkillValidationResult:
        if contract.name in self._skills:
            raise SkillAlreadyRegisteredError(contract.name)
        result = SkillValidationResult(valid=True)
        if validate:
            result = self._validator.validate(contract)
            if not result.valid:
                return result
        self._skills[contract.name] = SkillRecord(
            contract=contract,
            handler=handler,
            metadata=dict(metadata or {}),
        )
        self._lifecycle.install(contract.skill_id, version=contract.version)
        self._lifecycle.activate(contract.skill_id, reason="registered")
        return result

    def remove(self, skill_id: str) -> bool:
        # Accept either the skill_id or its name as a lookup key.
        key = self._resolve_key(skill_id)
        if key is None:
            return False
        rec = self._skills.pop(key)
        self._lifecycle.remove(rec.contract.skill_id, reason="unregistered")
        return True

    # ------------------------------------------------------------------
    # Lookup / state
    # ------------------------------------------------------------------
    def get(self, skill_id: str) -> SkillContract:
        return self._require(skill_id).contract

    def get_handler(self, skill_id: str) -> SkillHandler:
        return self._require(skill_id).handler

    def list(self) -> list[str]:
        return list(self._skills.keys())

    def list_enabled(self) -> list[str]:
        return [
            name
            for name, r in self._skills.items()
            if r.enabled and self._lifecycle.is_active(r.contract.skill_id)
        ]

    def is_enabled(self, skill_id: str) -> bool:
        rec = self._lookup(skill_id)
        if rec is None:
            return False
        return bool(rec.enabled and self._lifecycle.is_active(rec.contract.skill_id))

    def disable(self, skill_id: str) -> None:
        rec = self._require(skill_id)
        rec.enabled = False
        self._lifecycle.deactivate(rec.contract.skill_id, reason="disabled")

    def enable(self, skill_id: str) -> None:
        rec = self._require(skill_id)
        rec.enabled = True
        self._lifecycle.activate(rec.contract.skill_id, reason="enabled")

    def upgrade_version(self, skill_id: str, new_version: str) -> str:
        rec = self._require(skill_id)
        rec.contract = rec.contract.model_copy(update={"version": new_version})
        self._lifecycle.upgrade_version(rec.contract.skill_id, new_version)
        return new_version

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------
    async def execute(
        self,
        skill_id: str,
        arguments: dict[str, Any],
        *,
        caller_permissions: set[str] | None = None,
        context: Any | None = None,
    ) -> Any:
        rec = self._require(skill_id)
        return await self._executor.execute(
            rec.contract,
            rec.handler,
            arguments,
            caller_permissions=caller_permissions,
            context=context,
        )

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------
    def snapshot(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name, r in self._skills.items():
            state = self._lifecycle.state(r.contract.skill_id)
            out.append(
                {
                    "skill_id": r.contract.skill_id,
                    "name": r.contract.name,
                    "version": r.contract.version,
                    "enabled": r.enabled,
                    "lifecycle_state": state.value if state else None,
                    "permissions": list(r.contract.permissions),
                    "risk_level": r.contract.risk_level.value,
                }
            )
        return out

    # ------------------------------------------------------------------
    def _resolve_key(self, identifier: str) -> str | None:
        """Given an identifier that is either the skill name OR the skill_id,
        return the dict key (which is the skill name)."""
        if identifier in self._skills:
            return identifier
        for name, rec in self._skills.items():
            if rec.contract.skill_id == identifier:
                return name
        return None

    def _lookup(self, identifier: str) -> SkillRecord | None:
        key = self._resolve_key(identifier)
        return self._skills.get(key) if key else None

    def _require(self, identifier: str) -> SkillRecord:
        rec = self._lookup(identifier)
        if rec is None:
            raise SkillNotFoundError(identifier)
        return rec

    # ------------------------------------------------------------------
    @property
    def executor(self) -> SkillExecutor:
        return self._executor

    @property
    def lifecycle(self) -> SkillLifecycle:
        return self._lifecycle

    @property
    def validator(self) -> SkillValidator:
        return self._validator
