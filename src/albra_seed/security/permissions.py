"""Permissions — RBAC grant/revoke/has checks.

Permission chain in ALBRA:

    ROLE  →  PERMISSION  →  CAPABILITY  →  TOOL  →  ACTION

A ROLE bundles PERMISSIONS.
A PERMISSION authorizes a CAPABILITY (e.g. "execute external http calls").
A CAPABILITY maps to one or more TOOLS.
A TOOL is invoked by an ACTION (a ToolCall).

This file models the ROLE → PERMISSION → CAPABILITY segment. The firewall
and policy engine complete the chain (CAPABILITY → TOOL → ACTION).

Permissions are STRINGS (not enums): specialization layers define their
own capability namespace (e.g. "tool.execute.http", "memory.write.factual",
"approval.escalate"). The seed ships NO domain permissions.

DEFAULT-DENY: `has(role, perm)` returns False unless explicitly granted.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now
from albra_seed.security.roles import Role, RoleRegistry
from datetime import datetime


class Permission(BaseModel):
    """A single permission (capability grant).

    The `id` is the dotted capability path (e.g. "tool.execute.http").
    Permissions are hierarchical: granting "tool.execute" implies
    "tool.execute.http" and "tool.execute.shell" (use `matches`).

    `is_dangerous` flags permissions that should require HITL even when
    autonomy is high (DEFAULT-DENY on dangerous permissions).
    """

    permission_id: str = Field(default_factory=lambda: new_id("perm"))
    id: str  # dotted capability path, e.g. "tool.execute.http"
    description: str = ""
    is_dangerous: bool = False
    requires_audit: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    def matches(self, requested: str) -> bool:
        """Hierarchical match: 'tool.execute' matches 'tool.execute.http'.

        A permission matches if the requested id is equal to or starts
        with this permission's id followed by '.'.
        """
        if self.id == requested:
            return True
        prefix = self.id if self.id.endswith(".") else self.id + "."
        return requested.startswith(prefix)


class PermissionRegistry:
    """Registry of permissions + role→permission grants.

    DEFAULT-DENY: `has(role, perm)` returns False unless an explicit
    grant exists (or a parent permission in the hierarchy covers it).
    """

    def __init__(self, roles: RoleRegistry | None = None) -> None:
        self._permissions: dict[str, Permission] = {}
        # role_id -> set(permission_id)
        self._grants: dict[str, set[str]] = {}
        self.roles: RoleRegistry = roles or RoleRegistry()

    # -- permission catalogue ------------------------------------------------

    def register(self, permission: Permission) -> Permission:
        self._permissions[permission.id] = permission
        return permission

    def get_permission(self, permission_id: str) -> Permission | None:
        return self._permissions.get(permission_id)

    def list_permissions(self) -> list[Permission]:
        return list(self._permissions.values())

    # -- grants --------------------------------------------------------------

    def grant(self, role: Role | str, permission: Permission | str) -> None:
        """Grant a permission to a role. Idempotent."""
        role_id = role.role_id if isinstance(role, Role) else role
        perm_id = permission.id if isinstance(permission, Permission) else permission
        # Validate role exists (auto-create anon-friendly grants require the role).
        # We do NOT auto-register unknown roles — explicit is better than implicit.
        if self.roles.get(role_id) is None:
            raise KeyError(f"cannot grant to unknown role: {role_id}")
        # Register permission id if a string id was used and is known.
        if isinstance(permission, str) and permission not in self._permissions:
            # Auto-register a stub permission record (callers can enrich later).
            self._permissions[permission] = Permission(id=permission)
        self._grants.setdefault(role_id, set()).add(perm_id)
        # Mirror onto the Role object too for introspection.
        role_obj = self.roles.get(role_id)
        if role_obj is not None:
            role_obj.grant(perm_id)

    def revoke(self, role: Role | str, permission: Permission | str) -> bool:
        role_id = role.role_id if isinstance(role, Role) else role
        perm_id = permission.id if isinstance(permission, Permission) else permission
        granted = self._grants.get(role_id)
        if granted is None or perm_id not in granted:
            return False
        granted.discard(perm_id)
        role_obj = self.roles.get(role_id)
        if role_obj is not None:
            role_obj.revoke(perm_id)
        return True

    def has(self, role: Role | str, permission: Permission | str) -> bool:
        """DEFAULT-DENY: True only if an explicit grant (or a hierarchical
        parent permission in the same role) covers the requested permission.
        """
        role_id = role.role_id if isinstance(role, Role) else role
        requested = permission.id if isinstance(permission, Permission) else permission
        granted = self._grants.get(role_id, set())
        # Direct match.
        if requested in granted:
            return True
        # Hierarchical: does any granted permission cover `requested`?
        for granted_perm_id in granted:
            perm = self._permissions.get(granted_perm_id)
            if perm is not None and perm.matches(requested):
                return True
            # Manual hierarchical check (covers string-only grants).
            prefix = (
                granted_perm_id
                if granted_perm_id.endswith(".")
                else granted_perm_id + "."
            )
            if requested.startswith(prefix):
                return True
        return False

    def has_any(self, role_ids: list[str], permission: Permission | str) -> bool:
        """True if ANY of the roles has the permission."""
        return any(self.has(rid, permission) for rid in role_ids)


__all__ = ["Permission", "PermissionRegistry"]
