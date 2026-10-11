"""Memory isolation by (agent_id, domain, user_id, context_id).

Strict isolation prevents cross-agent, cross-domain, cross-user, and
cross-context memory leakage. Every query/write routes through this gate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryKey:
    """Composite key for memory isolation. None values are normalized to ""."""

    agent_id: str
    domain: str = "default"
    user_id: str | None = None
    context_id: str | None = None

    def parts(self) -> tuple[str, str, str, str]:
        return (
            self.agent_id,
            self.domain,
            self.user_id or "",
            self.context_id or "",
        )

    def __str__(self) -> str:
        a, d, u, c = self.parts()
        return f"mem::{a}::{d}::{u}::{c}"


class MemoryIsolator:
    """Enforces strict isolation across (agent_id, domain, user_id, context_id).

    Two memories are considered in the same bucket if and only if all four
    components match. The isolator also produces a composite index key that
    stores can use as a column or composite index.
    """

    @staticmethod
    def key_for(
        agent_id: str,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
    ) -> MemoryKey:
        if not agent_id:
            raise ValueError("agent_id is required for memory isolation")
        return MemoryKey(
            agent_id=agent_id,
            domain=domain or "default",
            user_id=user_id,
            context_id=context_id,
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
    ) -> None:
        a = MemoryIsolator.key_for(
            record_agent_id, record_domain, record_user_id, record_context_id
        )
        b = MemoryIsolator.key_for(
            query_agent_id, query_domain, query_user_id, query_context_id
        )
        if a != b:
            raise PermissionError(
                "memory isolation violation: record does not belong to "
                f"requested bucket (record={a!s} query={b!s})"
            )
