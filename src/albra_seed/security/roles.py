"""RBAC Roles for the ALBRA SEED.

NOTE: This `Role` is distinct from `albra_seed.core.types.Role` (which is
the conversational role: system | user | assistant | tool | observer).
This `Role` is the RBAC role: a named bundle of permissions granted to
principals (agents, users, services).

A Role has:
  - id           : stable identifier (e.g. "admin", "analyst", "readonly")
  - name         : human-readable name
  - permissions  : list of permission ids granted by this role
  - description  : free-form description (NO domain knowledge in seed roles)

The seed ships NO domain roles. Specialization layers register roles via
`RoleRegistry.register(role)`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now
from datetime import datetime


class Role(BaseModel):
    """RBAC Role — a named bundle of permissions.

    Roles group permissions so principals can be granted many at once
    rather than enumerating each capability. A role NEVER implies
    autonomy — autonomy is a separate axis (see `albra_seed.autonomy`).
    """

    role_id: str = Field(default_factory=lambda: new_id("role"))
    name: str
    description: str = ""
    permissions: list[str] = Field(default_factory=list)
    parent_role_id: str | None = None  # for role hierarchy
    is_system: bool = False  # system roles cannot be deleted
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    def has_permission(self, permission_id: str) -> bool:
        return permission_id in self.permissions

    def grant(self, permission_id: str) -> None:
        if permission_id not in self.permissions:
            self.permissions.append(permission_id)

    def revoke(self, permission_id: str) -> None:
        if permission_id in self.permissions:
            self.permissions.remove(permission_id)


class RoleRegistry:
    """In-memory registry of RBAC roles.

    The seed ships with one built-in system role: `anonymous` (empty
    permissions). Specialization layers add domain-specific roles.
    """

    def __init__(self) -> None:
        self._roles: dict[str, Role] = {}
        # Built-in anonymous role — never deleted, no permissions.
        anon = Role(
            role_id="anonymous",
            name="anonymous",
            description="Default role for unauthenticated principals. No permissions.",
            is_system=True,
        )
        self._roles[anon.role_id] = anon

    def register(self, role: Role) -> Role:
        if role.role_id in self._roles and self._roles[role.role_id].is_system:
            raise ValueError(
                f"cannot overwrite system role: {role.role_id}"
            )
        self._roles[role.role_id] = role
        return role

    def get(self, role_id: str) -> Role | None:
        return self._roles.get(role_id)

    def require(self, role_id: str) -> Role:
        role = self.get(role_id)
        if role is None:
            raise KeyError(f"role not found: {role_id}")
        return role

    def list_roles(self) -> list[Role]:
        return list(self._roles.values())

    def delete(self, role_id: str) -> bool:
        role = self._roles.get(role_id)
        if role is None or role.is_system:
            return False
        del self._roles[role_id]
        return True

    def permissions_for(self, role_ids: list[str]) -> set[str]:
        """Collect all permissions across the given roles + their parents."""
        seen_roles: set[str] = set()
        permissions: set[str] = set()
        stack = list(role_ids)
        while stack:
            rid = stack.pop()
            if rid in seen_roles:
                continue
            seen_roles.add(rid)
            role = self._roles.get(rid)
            if role is None:
                continue
            permissions.update(role.permissions)
            if role.parent_role_id:
                stack.append(role.parent_role_id)
        return permissions


__all__ = ["Role", "RoleRegistry"]
