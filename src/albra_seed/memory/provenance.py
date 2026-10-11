"""Provenance attachment for every memory write.

Every memory record MUST carry provenance. This module builds a `Provenance`
model from a memory write and links it back to its evidence chain. Factual
memory (is_fact=True) requires at least one evidence_id.

This module is the SINGLE SOURCE OF TRUTH for the FACT rule. Use
`MemoryProvenance.fact_rule()` to obtain the canonical rule and
`MemoryProvenance.validate_fact(record)` to apply it. No other module should
re-implement FACT validation logic.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.base import MemoryRecord
from albra_seed.core.types import Provenance, new_id, utc_now


class FactRule(BaseModel):
    """The UNIVERSAL FACT rule. Single source of truth for is_fact validation.

    A `MemoryRecord` with `is_fact=True` MUST satisfy:
        - require_evidence: at least one evidence_id backing the fact
        - source_type in allowed_source_types: only trustworthy sources
        - confidence >= min_confidence: high confidence required for facts
    """

    require_evidence: bool = Field(default=True)
    allowed_source_types: frozenset[str] = Field(
        default_factory=lambda: frozenset({"tool", "external", "verified_provider"})
    )
    min_confidence: float = Field(default=0.9, ge=0.0, le=1.0)


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
    def fact_rule() -> FactRule:
        """The UNIVERSAL fact rule. Single source of truth.

        A MemoryRecord with is_fact=True MUST satisfy:
            - evidence_ids non-empty (at least one evidence backing the fact)
            - source_type in {"tool", "external", "verified_provider"}
              (only trustworthy sources)
            - confidence >= 0.9 (high confidence required for facts)

        Returns a FactRule instance.
        """
        return FactRule(
            require_evidence=True,
            allowed_source_types=frozenset({"tool", "external", "verified_provider"}),
            min_confidence=0.9,
        )

    @staticmethod
    def validate_fact(record: MemoryRecord) -> tuple[bool, str | None]:
        """Validate a MemoryRecord against the UNIVERSAL FACT rule.

        Returns ``(is_valid, reason_if_invalid)``. A non-fact record
        (``is_fact=False``) always passes; the rule applies only when
        ``is_fact=True``.

        This is the SINGLE entry point for FACT validation. All call sites
        (``MemoryManager.remember``, consolidation gates, future API
        surfaces) MUST delegate here rather than re-implementing checks.
        """
        if not record.is_fact:
            return True, None  # non-fact records skip the rule
        rule = MemoryProvenance.fact_rule()
        if rule.require_evidence and not record.evidence_ids:
            return False, "is_fact=True requires at least one evidence_id"
        if record.source_type not in rule.allowed_source_types:
            return (
                False,
                f"is_fact=True requires source_type in "
                f"{sorted(rule.allowed_source_types)} (got {record.source_type!r})",
            )
        if record.confidence < rule.min_confidence:
            return (
                False,
                f"is_fact=True requires confidence >= {rule.min_confidence} "
                f"(got {record.confidence})",
            )
        return True, None

    @staticmethod
    def to_dict(provenance: Provenance) -> dict[str, Any]:
        return provenance.model_dump(mode="json")
