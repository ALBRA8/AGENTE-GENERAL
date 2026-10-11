"""Provenance attachment for every memory write.

Every memory record MUST carry provenance. This module builds a `Provenance`
model from a memory write and links it back to its evidence chain. Factual
memory (is_fact=True) requires at least one evidence_id.
"""

from __future__ import annotations

from typing import Any

from albra_seed.core.base import MemoryRecord
from albra_seed.core.types import Provenance, new_id, utc_now


class MemoryProvenance:
    """Attaches provenance to every memory write.

    Records:
        - source / source_type
        - timestamp
        - confidence (0.0 .. 1.0)
        - evidence_ids (chain)
        - version + modified_at
    """

    def __init__(self, *, default_source: str = "agent", default_source_type: str = "agent") -> None:
        self.default_source = default_source
        self.default_source_type = default_source_type

    def build(
        self,
        *,
        source: str | None = None,
        source_type: str | None = None,
        confidence: float = 0.0,
        evidence_ids: list[str] | None = None,
        execution_id: str | None = None,
        correlation_id: str | None = None,
        parent_provenance_id: str | None = None,
    ) -> Provenance:
        return Provenance(
            provenance_id=new_id("prov"),
            source=source or self.default_source,
            source_type=source_type or self.default_source_type,
            timestamp=utc_now(),
            confidence=max(0.0, min(1.0, float(confidence))),
            evidence_ids=list(evidence_ids or []),
            execution_id=execution_id,
            correlation_id=correlation_id,
            parent_provenance_id=parent_provenance_id,
            version=1,
            modified_at=None,
        )

    def attach(
        self,
        record: MemoryRecord,
        *,
        provenance: Provenance | None = None,
    ) -> tuple[MemoryRecord, Provenance]:
        """Attach provenance to a memory record (returns the updated record
        and the Provenance object)."""
        if provenance is None:
            provenance = self.build(
                source=record.source,
                source_type=record.source_type,
                confidence=record.confidence,
                evidence_ids=record.evidence_ids,
                execution_id=record.execution_id,
                correlation_id=record.correlation_id,
            )
        record.provenance_id = provenance.provenance_id
        record.version = max(1, record.version)
        record.modified_at = utc_now()
        return record, provenance

    @staticmethod
    def requires_evidence_for_fact(record: MemoryRecord) -> bool:
        """Factual memory (is_fact=True) must reference at least one evidence id."""
        if record.is_fact:
            return bool(record.evidence_ids)
        return True

    @staticmethod
    def to_dict(provenance: Provenance) -> dict[str, Any]:
        return provenance.model_dump(mode="json")
