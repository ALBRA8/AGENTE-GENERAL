"""Access Control List (ACL) for audit log access.

Audit logs contain sensitive operational data (executions, errors,
tool outputs, policy decisions). Access to read/forward/export them
MUST be gated by an explicit ACL.

DEFAULT-DENY: a principal not explicitly listed in the ACL CANNOT
read any audit entry. Even the system role must be granted explicitly.

ACL structure:
    subject  -> list of (resource_pattern, action)
    where:
      subject        = principal_id | role_id | "*"
      resource_pattern = glob-style, e.g. "audit:agent:albra-*"
      action         = "read" | "export" | "forward" | "*"

The seed ships NO subjects by default. Specialization layers grant
access explicitly.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now
from datetime import datetime


class ACLEntry(BaseModel):
    """One grant in the audit ACL."""

    entry_id: str = Field(default_factory=lambda: new_id("acl"))
    subject: str  # principal_id, role_id, or "*"
    resource_pattern: str  # glob, e.g. "audit:agent:*"
    action: str = "read"  # read | export | forward | "*"
    granted_by: str = "system"
    granted_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AccessControlList:
    """Audit log ACL — DEFAULT-DENY.

    `can(subject, action, resource)` returns True ONLY IF an explicit
    non-expired entry matches the subject (or "*") AND the resource
    pattern AND the action (or "*").
    """

    def __init__(self) -> None:
        self._entries: list[ACLEntry] = []

    def grant(self, entry: ACLEntry) -> ACLEntry:
        self._entries.append(entry)
        return entry

    def revoke(self, entry_id: str) -> bool:
        for i, e in enumerate(self._entries):
            if e.entry_id == entry_id:
                del self._entries[i]
                return True
        return False

    def list_entries(self) -> list[ACLEntry]:
        return list(self._entries)

    def can(
        self,
        subject: str,
        action: str,
        resource: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        """DEFAULT-DENY. True only if an explicit grant matches."""
        now = now or utc_now()
        for e in self._entries:
            if e.expires_at is not None and now > e.expires_at:
                continue
            # Subject match: exact or wildcard.
            if e.subject != "*" and e.subject != subject:
                continue
            # Action match: exact or wildcard.
            if e.action != "*" and e.action != action:
                continue
            # Resource match: glob.
            if fnmatch.fnmatchcase(resource, e.resource_pattern):
                return True
        return False

    def subjects_for(self, action: str, resource: str) -> list[str]:
        """Return all subjects explicitly granted access (for audit UI)."""
        result: list[str] = []
        for e in self._entries:
            if e.action not in {action, "*"}:
                continue
            if fnmatch.fnmatchcase(resource, e.resource_pattern):
                result.append(e.subject)
        return result


__all__ = ["ACLEntry", "AccessControlList"]
