"""Evidence contract — formal model extending Evidence with validation rules.

Rules (NON-NEGOTIABLE):
    - FACT requires confidence >= FACT_MIN_CONFIDENCE (0.9)
      AND source_type in FACT_ALLOWED_SOURCES = {tool, external, verified_provider}
    - INFERENCE never auto-promoted to FACT
    - UNKNOWN never auto-promoted to FACT
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import Evidence, EvidenceType, new_id, utc_now


FACT_MIN_CONFIDENCE: float = 0.9
FACT_ALLOWED_SOURCES: frozenset[str] = frozenset(
    {"tool", "external", "verified_provider"}
)


class EvidenceValidationError(ValueError):
    """Raised when evidence fails contract validation."""


class EvidenceContract(BaseModel):
    """Formal evidence contract.

    Validates the Evidence type rules. The contract never silently promotes
    an INFERENCE or UNKNOWN to FACT.
    """

    min_confidence_for_fact: float = Field(default=FACT_MIN_CONFIDENCE)
    allowed_sources_for_fact: frozenset[str] = Field(default=FACT_ALLOWED_SOURCES)

    def validate(self, evidence: Evidence) -> Evidence:
        """Validate an Evidence object against the contract. Returns the
        evidence unchanged. Raises EvidenceValidationError on violation."""
        if evidence.confidence < 0.0 or evidence.confidence > 1.0:
            raise EvidenceValidationError(
                f"confidence must be in [0,1]; got {evidence.confidence}"
            )
        if evidence.evidence_type == EvidenceType.FACT:
            if evidence.confidence < self.min_confidence_for_fact:
                raise EvidenceValidationError(
                    f"FACT requires confidence >= {self.min_confidence_for_fact}; "
                    f"got {evidence.confidence}"
                )
            if evidence.source_type not in self.allowed_sources_for_fact:
                raise EvidenceValidationError(
                    f"FACT requires source_type in {sorted(self.allowed_sources_for_fact)}; "
                    f"got {evidence.source_type!r}"
                )
        return evidence

    def can_promote_to_fact(self, evidence: Evidence) -> bool:
        """Return True iff this evidence meets all FACT requirements.
        Note: even when True, the caller must explicitly request promotion;
        the contract never silently promotes INFERENCE or UNKNOWN.
        """
        if evidence.evidence_type == EvidenceType.FACT:
            return True
        if evidence.confidence < self.min_confidence_for_fact:
            return False
        if evidence.source_type not in self.allowed_sources_for_fact:
            return False
        return True

    def promote_to_fact(self, evidence: Evidence) -> Evidence:
        """Explicit promotion gate.

        Only INFERENCE evidence from a qualifying source at qualifying
        confidence may be promoted — and the caller must explicitly invoke
        this. UNKNOWN is NEVER auto-promoted.
        """
        if evidence.evidence_type == EvidenceType.UNKNOWN:
            raise EvidenceValidationError(
                "UNKNOWN evidence cannot be promoted to FACT (contract hard rule)"
            )
        if evidence.evidence_type == EvidenceType.FACT:
            return self.validate(evidence)
        if not self.can_promote_to_fact(evidence):
            raise EvidenceValidationError(
                "evidence does not meet FACT requirements: "
                f"confidence={evidence.confidence} source_type={evidence.source_type!r}"
            )
        evidence.evidence_type = EvidenceType.FACT
        evidence.timestamp = utc_now()
        return self.validate(evidence)

    def new_fact(
        self,
        *,
        source: str,
        source_type: str,
        content: Any = None,
        content_ref: str | None = None,
        confidence: float = 1.0,
        execution_id: str | None = None,
        evidence_ids: list[str] | None = None,
    ) -> Evidence:
        ev = Evidence(
            evidence_id=new_id("ev"),
            source=source,
            source_type=source_type,
            content=content,
            content_ref=content_ref,
            evidence_type=EvidenceType.FACT,
            confidence=confidence,
            provenance=list(evidence_ids or []),
            execution_id=execution_id,
        )
        return self.validate(ev)

    def new_inference(
        self,
        *,
        source: str,
        source_type: str = "agent",
        content: Any = None,
        confidence: float = 0.5,
        execution_id: str | None = None,
    ) -> Evidence:
        ev = Evidence(
            evidence_id=new_id("ev"),
            source=source,
            source_type=source_type,
            content=content,
            evidence_type=EvidenceType.INFERENCE,
            confidence=confidence,
            execution_id=execution_id,
        )
        return self.validate(ev)

    def new_unknown(
        self,
        *,
        source: str,
        source_type: str = "agent",
        content: Any = None,
        execution_id: str | None = None,
    ) -> Evidence:
        ev = Evidence(
            evidence_id=new_id("ev"),
            source=source,
            source_type=source_type,
            content=content,
            evidence_type=EvidenceType.UNKNOWN,
            confidence=0.0,
            execution_id=execution_id,
        )
        return self.validate(ev)
