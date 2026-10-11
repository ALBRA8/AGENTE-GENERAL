"""Consolidation policy for memory.

Promotes episodic → semantic when confidence >= threshold.
NEVER promotes UNKNOWN → FACT. The promotion only operates on the
EPISODIC → SEMANTIC axis. FACT promotion is gated by the evidence
contract, not by consolidation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from albra_seed.core.base import MemoryRecord
from albra_seed.core.types import MemoryType


class ConsolidationResult(BaseModel):
    promoted: bool = False
    record_id: str
    old_type: MemoryType
    new_type: MemoryType | None = None
    reason: str = ""


class ConsolidationPolicy(BaseModel):
    """Promotes episodic → semantic when confidence >= threshold.

    Configuration:
        - confidence_threshold (default 0.7): minimum confidence to promote
        - min_access_count (default 0): minimum times accessed
        - require_evidence (default False): whether at least one evidence id is required

    Hard rule: NEVER promotes a record whose source evidence_type is UNKNOWN
    into a FACT. Consolidation operates EPISODIC → SEMANTIC only.
    """

    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    min_access_count: int = Field(default=0, ge=0)
    require_evidence: bool = False
    allow_fact_promotion: bool = False  # disabled by design; FACT gated elsewhere

    def should_consolidate(self, record: MemoryRecord) -> bool:
        if record.memory_type != MemoryType.EPISODIC:
            return False
        if record.confidence < self.confidence_threshold:
            return False
        if self.require_evidence and not record.evidence_ids:
            return False
        # never auto-promote an UNKNOWN-tagged record to FACT
        # (record itself doesn't carry evidence_type, but is_fact flag reflects
        #  the underlying evidence). If is_fact is False we cannot fabricate it.
        return True

    def consolidate(self, record: MemoryRecord) -> ConsolidationResult:
        if not self.should_consolidate(record):
            return ConsolidationResult(
                promoted=False,
                record_id=record.record_id,
                old_type=record.memory_type,
                reason="policy conditions not met",
            )
        old = record.memory_type
        record.memory_type = MemoryType.SEMANTIC
        record.version += 1
        # is_fact stays unchanged — consolidation never auto-promotes to FACT
        return ConsolidationResult(
            promoted=True,
            record_id=record.record_id,
            old_type=old,
            new_type=MemoryType.SEMANTIC,
            reason="confidence threshold met",
        )

    def can_promote_to_fact(self, record: MemoryRecord) -> bool:
        """Hard gate: this policy never promotes anything to FACT.
        FACT promotion must go through the EvidenceContract instead.
        """
        return False
