"""Memory isolation by (org_id, agent_id, domain, user_id, context_id).

Strict isolation prevents cross-organization (T9, S4.4), cross-agent,
cross-domain, cross-user, and cross-context memory leakage. Every
query/write routes through this gate.

T9 tenancy rules (fail-closed):

* ``org_id`` is the FIRST component of the isolation key. ``None`` is
  normalized to "" (the legacy bucket) so records written before S4.4
  remain readable and never mix with org-scoped ones.
* ``assert_org_consistent`` binds the organization to the agent identity:
  when ``agent_id`` carries the tenant identity prefix ``org:<X>``
  (D-014), any explicitly provided ``org_id`` MUST equal ``X`` — a
  mismatch is a tenant-boundary violation, not a soft warning.
* ``org_id`` values must match the tenant organization format ``org_<id>``
  shared with the platform contracts. Malformed values are rejected
  before touching any store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tenant organization identifier pattern (org_<id>) — shared with the
# platform contracts (packages/contracts/src/context.ts ORGANIZATION_ID).
ORG_ID_PATTERN = re.compile(r"^org_[A-Za-z0-9_-]+$")

# Tenant agent identity prefix: "org:<organizationId>#" or "org:<organizationId>"
# (D-014 logical agent identity).
AGENT_ORG_PREFIX_PATTERN = re.compile(r"^org:([A-Za-z0-9_-]+)(?:#|$)")


@dataclass(frozen=True)
class MemoryKey:
    """Composite key for memory isolation. None values are normalized to ""."""

    agent_id: str
    domain: str = "default"
    user_id: str | None = None
    context_id: str | None = None
    org_id: str | None = None

    def parts(self) -> tuple[str, str, str, str, str]:
        return (
            self.org_id or "",
            self.agent_id,
            self.domain,
            self.user_id or "",
            self.context_id or "",
        )

    def __str__(self) -> str:
        o, a, d, u, c = self.parts()
        if not o:
            # Legacy format (pre-T9): keeps composite index keys byte-for-byte
            # compatible with keys written before S4.4.
            return f"mem::{a}::{d}::{u}::{c}"
        return f"mem::org={o}::{a}::{d}::{u}::{c}"


class MemoryIsolator:
    """Enforces strict isolation across (org_id, agent_id, domain, user_id,
    context_id).

    Two memories are considered in the same bucket if and only if all five
    components match. The isolator also produces a composite index key that
    stores can use as a column or composite index.
    """

    @staticmethod
    def key_for(
        agent_id: str,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        org_id: str | None = None,
    ) -> MemoryKey:
        if not agent_id:
            raise ValueError("agent_id is required for memory isolation")
        if org_id is not None:
            MemoryIsolator.validate_org_id(org_id)
            MemoryIsolator.assert_org_consistent(agent_id, org_id)
        return MemoryKey(
            agent_id=agent_id,
            domain=domain or "default",
            user_id=user_id,
            context_id=context_id,
            org_id=org_id,
        )

    @staticmethod
    def validate_org_id(org_id: str) -> str:
        """Validate the tenant organization format (``org_<id>``); fail-closed."""
        if not isinstance(org_id, str) or not ORG_ID_PATTERN.match(org_id):
            raise ValueError(
                "org_id must match the tenant organization format 'org_<id>' "
                f"(got {type(org_id).__name__})"
            )
        return org_id

    @staticmethod
    def org_of_agent(agent_id: str) -> str | None:
        """Extract the organization encoded in a D-014 agent identity.

        Returns ``None`` when the agent_id carries no ``org:<X>`` prefix
        (e.g. the brain's own ``albra-agent`` identity).
        """
        match = AGENT_ORG_PREFIX_PATTERN.match(agent_id or "")
        return match.group(1) if match else None

    @staticmethod
    def assert_org_consistent(agent_id: str, org_id: str | None) -> None:
        """Tenant-boundary check: org_id MUST match the org encoded in
        agent_id when both are present (D-014 identity vs tenant context).

        Raises PermissionError on mismatch — a cross-tenant write/read
        attempt is a violation, never a warning.
        """
        if org_id is None:
            return
        MemoryIsolator.validate_org_id(org_id)
        encoded = MemoryIsolator.org_of_agent(agent_id)
        if encoded is not None and encoded != org_id:
            raise PermissionError(
                "memory isolation violation: agent identity belongs to a "
                f"different organization (agent={agent_id!r} org={org_id!r})"
            )

    @staticmethod
    def index_key(key: MemoryKey) -> str:
        return str(key)

    @staticmethod
    def same_bucket(
        a: tuple[str, str, str | None, str | None],
        b: tuple[str, str, str | None, str | None],
    ) -> bool:
        a_key = MemoryIsolator.key_for(a[0], a[1], a[2], a[3])
        b_key = MemoryIsolator.key_for(b[0], b[1], b[2], b[3])
        return a_key == b_key

    @staticmethod
    def assert_isolated(
        record_agent_id: str,
        record_domain: str,
        record_user_id: str | None,
        record_context_id: str | None,
        query_agent_id: str,
        query_domain: str = "default",
        query_user_id: str | None = None,
        query_context_id: str | None = None,
        record_org_id: str | None = None,
        query_org_id: str | None = None,
    ) -> None:
        a = MemoryIsolator.key_for(
            record_agent_id, record_domain, record_user_id, record_context_id,
            record_org_id,
        )
        b = MemoryIsolator.key_for(
            query_agent_id, query_domain, query_user_id, query_context_id,
            query_org_id,
        )
        if a != b:
            raise PermissionError(
                "memory isolation violation: record does not belong to "
                f"requested bucket (record={a!s} query={b!s})"
            )
