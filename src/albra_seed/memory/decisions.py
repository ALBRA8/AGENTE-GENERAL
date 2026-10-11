"""DecisionRecord — a structured wrapper around MemoryRecord.

This is NOT a separate memory system. A DecisionRecord is stored as a
MemoryRecord with `memory_type=MemoryType.DECISION`; the decision-specific
fields (objective, options_considered, decision_taken, reasoning, outcome,
feedback_ids, ...) live inside the MemoryRecord `content` dict.

The wrapper exists so callers can think in decision terms while the
underlying MemoryDV storage, isolation, provenance and lifecycle remain
the single source of truth.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.base import MemoryRecord
from albra_seed.core.types import MemoryType, new_id, utc_now


class DecisionRecord(BaseModel):
    """A decision record. Stored as MemoryRecord(content=..., memory_type=DECISION).

    Fields map 1:1 to the structured payload that is JSON-serialized into
    `MemoryRecord.content`. `decision_id` is the same string as the
    underlying MemoryRecord.record_id so callers can round-trip records
    without maintaining a separate id space.
    """

    decision_id: str = Field(default_factory=lambda: new_id("dec"))
    agent_id: str
    context_id: str | None = None
    objective: str
    options_considered: list[dict[str, Any]] = Field(default_factory=list)
    decision_taken: str
    reasoning: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    execution_id: str | None = None
    correlation_id: str | None = None
    outcome: str | None = None  # filled later by feedback
    feedback_ids: list[str] = Field(default_factory=list)  # links to FeedbackRecord
    confidence: float = 0.0
    timestamp: datetime = Field(default_factory=utc_now)

    # --- conversion to / from MemoryRecord ---------------------------------

    def to_memory_record(self, domain: str = "default") -> MemoryRecord:
        """Convert to a MemoryRecord for storage in a MemoryStore.

        `decision_id` is preserved as the record_id so reads and writes
        round-trip through the same key.
        """
        return MemoryRecord(
            record_id=self.decision_id,
            agent_id=self.agent_id,
            domain=domain,
            context_id=self.context_id,
            memory_type=MemoryType.DECISION,
            content=self.model_dump(mode="json"),
            confidence=self.confidence,
            source="agent",
            source_type="agent",
            evidence_ids=list(self.evidence_ids),
            execution_id=self.execution_id,
            correlation_id=self.correlation_id,
        )

    @classmethod
    def from_memory_record(cls, record: MemoryRecord) -> DecisionRecord:
        """Reconstruct a DecisionRecord from a stored MemoryRecord.

        The MemoryRecord.content must be a dict that matches the
        DecisionRecord schema. Raises TypeError if the record is not a
        DECISION memory (defensive — keeps callers honest).
        """
        if record.memory_type != MemoryType.DECISION:
            raise TypeError(
                f"MemoryRecord is not a DECISION memory (got {record.memory_type!r})"
            )
        payload = record.content
        if not isinstance(payload, dict):
            raise TypeError(
                "DecisionRecord.from_memory_record requires content to be a dict"
            )
        # Keep the canonical record_id from the storage layer; if the payload
        # already carries it (normal path), this is a no-op.
        payload = {**payload, "decision_id": record.record_id}
        return cls(**payload)
