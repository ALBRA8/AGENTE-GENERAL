"""ProvenanceChain — walk the provenance graph from any record back to its
sources.

Uses the parent_provenance_id chain plus the evidence_ids list (which
references Evidence records) to produce an ordered list of Provenance
records from newest back to the root sources.
"""

from __future__ import annotations

from albra_seed.core.types import Provenance
from albra_seed.provenance.store import ProvenanceStore


class ProvenanceChain:
    """Builds a provenance chain from a record_id back to its sources."""

    def __init__(self, store: ProvenanceStore) -> None:
        self.store = store

    async def trace(self, record_id: str, *, record_kind: str = "memory") -> list[Provenance]:
        """Trace a record back to its sources.

        1. Look up provenance(s) attached to (record_kind, record_id).
        2. For each, walk parent_provenance_id until none or cycle detected.
        3. Return ordered list: [newest, ..., oldest].
        """
        chain: list[Provenance] = []
        roots = await self.store.find_by_ref(record_kind, record_id)
        seen: set[str] = set()
        # Walk each root upward via parent_provenance_id
        for root in roots:
            stack: list[Provenance] = [root]
            while stack:
                node = stack.pop()
                if node.provenance_id in seen:
                    continue
                seen.add(node.provenance_id)
                chain.append(node)
                if node.parent_provenance_id:
                    parent = await self.store.get(node.parent_provenance_id)
                    if parent is not None:
                        stack.append(parent)
        # Stable order: most recently recorded first
        chain.sort(key=lambda p: p.timestamp, reverse=True)
        return chain

    async def trace_by_provenance_id(self, provenance_id: str) -> list[Provenance]:
        """Trace from a single provenance_id back to its sources."""
        start = await self.store.get(provenance_id)
        if start is None:
            return []
        chain: list[Provenance] = []
        seen: set[str] = set()
        node: Provenance | None = start
        while node is not None and node.provenance_id not in seen:
            seen.add(node.provenance_id)
            chain.append(node)
            if not node.parent_provenance_id:
                break
            node = await self.store.get(node.parent_provenance_id)
        return chain

    async def evidence_ids_in_chain(self, chain: list[Provenance]) -> list[str]:
        ids: list[str] = []
        for p in chain:
            for eid in p.evidence_ids:
                if eid not in ids:
                    ids.append(eid)
        return ids

    async def has_evidence(self, record_id: str, *, record_kind: str = "memory") -> bool:
        chain = await self.trace(record_id, record_kind=record_kind)
        return bool(await self.evidence_ids_in_chain(chain))
