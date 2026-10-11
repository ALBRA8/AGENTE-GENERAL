"""DecisionMemory — thin adapter over a MemoryStore.

This is NOT a separate memory system. `DecisionMemory` delegates every
read/write to a concrete `MemoryStore` (the same MemoryDV used by the
MemoryManager). Its only job is to translate between the structured
`DecisionRecord` view and the flat `MemoryRecord(memory_type=DECISION)`
representation that lives inside the underlying store.

Because it reuses MemoryStore as-is, decisions inherit:
    - strict isolation by (agent_id, domain, user_id, context_id)
    - provenance via ProvenanceTracker.track_decision
    - lifecycle / consolidation hooks (unchanged — they key off MemoryType)
"""

from __future__ import annotations

import contextlib
from typing import Any

from albra_seed.core.base import MemoryStore
from albra_seed.core.types import MemoryType, utc_now
from albra_seed.memory.decisions import DecisionRecord
from albra_seed.memory.provenance import MemoryProvenance
from albra_seed.provenance.tracker import ProvenanceTracker


class DecisionMemory:
    """Thin adapter that stores decisions inside a MemoryStore.

    Sibling of `MemoryManager` — they share the same store, the same
    isolation rules, and the same provenance discipline. Neither wraps
    nor replaces the other.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        provenance: MemoryProvenance | None = None,
        tracker: ProvenanceTracker | None = None,
    ) -> None:
        self.store = store
        self.provenance = provenance or MemoryProvenance()
        self.tracker = tracker or ProvenanceTracker()

    # --- write -------------------------------------------------------------

    async def record_decision(
        self,
        decision: DecisionRecord,
        *,
        domain: str = "default",
        user_id: str | None = None,
    ) -> str:
        """Persist a decision as a MemoryRecord(memory_type=DECISION).

        Attaches MemoryProvenance (in-process) AND calls
        ProvenanceTracker.track_decision so the decision is observable in
        the cross-cutting provenance store.

        Returns the decision_id (== MemoryRecord.record_id).
        """
        record = decision.to_memory_record(domain=domain)
        if user_id is not None:
            record.user_id = user_id
        record, _prov = self.provenance.attach(record)

        await self.store.write(record)

        # Cross-cutting provenance: register the decision kind so the
        # ProvenanceTracker has a record_ref for later lookup. This is best
        # effort — failures here MUST NOT corrupt the memory write that
        # already succeeded.
        with contextlib.suppress(Exception):
            await self.tracker.track_decision(
                decision=decision.decision_taken,
                record_ref=record.record_id,
                confidence=decision.confidence,
                evidence_ids=list(decision.evidence_ids),
                execution_id=decision.execution_id,
                correlation_id=decision.correlation_id,
            )

        return record.record_id

    # --- read --------------------------------------------------------------

    async def get_decision(self, decision_id: str) -> DecisionRecord | None:
        record = await self.store.read(decision_id)
        if record is None:
            return None
        if record.memory_type != MemoryType.DECISION:
            return None
        return DecisionRecord.from_memory_record(record)

    async def list_decisions(
        self,
        *,
        agent_id: str,
        domain: str = "default",
        context_id: str | None = None,
        user_id: str | None = None,
        limit: int = 50,
    ) -> list[DecisionRecord]:
        records = await self.store.query(
            agent_id=agent_id,
            domain=domain,
            user_id=user_id,
            context_id=context_id,
            memory_type=MemoryType.DECISION,
            limit=limit,
        )
        out: list[DecisionRecord] = []
        for r in records:
            try:
                out.append(DecisionRecord.from_memory_record(r))
            except (TypeError, ValueError):
                # Defensive: a malformed record must not poison listing.
                continue
        return out

    # --- update ------------------------------------------------------------

    async def link_outcome(
        self,
        decision_id: str,
        outcome: str,
        feedback_ids: list[str] | None = None,
    ) -> bool:
        """Attach an observed outcome (and optional feedback refs) to a
        previously-recorded decision.

        Implemented as read → mutate content → bump version → write back.
        Returns False if the decision was not found; True on success.
        """
        record = await self.store.read(decision_id)
        if record is None or record.memory_type != MemoryType.DECISION:
            return False

        decision = DecisionRecord.from_memory_record(record)
        decision.outcome = outcome
        if feedback_ids:
            # merge without duplicates, preserve order
            seen = set(decision.feedback_ids)
            for fid in feedback_ids:
                if fid not in seen:
                    decision.feedback_ids.append(fid)
                    seen.add(fid)

        # Reuse the existing provenance_id (do not create a new chain entry)
        # but bump version + modified_at so the store records a new revision.
        new_content: dict[str, Any] = decision.model_dump(mode="json")
        record.content = new_content
        record.version = (record.version or 1) + 1
        record.modified_at = utc_now()
        await self.store.write(record)
        return True
