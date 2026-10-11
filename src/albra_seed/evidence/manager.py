"""EvidenceManager — register, retrieve, validate evidence.

Enforces the EvidenceContract. Stamps every registered evidence with a
SHA-256 integrity hash. Never silently promotes INFERENCE or UNKNOWN to
FACT; FACT must meet confidence >= 0.9 AND source_type in
{tool, external, verified_provider}.
"""

from __future__ import annotations

from typing import Any

from albra_seed.core.types import Evidence, EvidenceType, new_id, utc_now
from albra_seed.evidence.contract import (
    EvidenceContract,
    EvidenceValidationError,
    FACT_ALLOWED_SOURCES,
    FACT_MIN_CONFIDENCE,
)
from albra_seed.evidence.integrity import EvidenceIntegrity
from albra_seed.evidence.store import EvidenceStore


class EvidenceManager:
    """High-level orchestrator over an EvidenceStore + EvidenceContract."""

    def __init__(
        self,
        store: EvidenceStore | None = None,
        *,
        contract: EvidenceContract | None = None,
    ) -> None:
        from albra_seed.evidence.store import InMemoryEvidenceStore

        self.store: EvidenceStore = store or InMemoryEvidenceStore()
        self.contract: EvidenceContract = contract or EvidenceContract()

    async def register(
        self,
        *,
        source: str,
        source_type: str,
        content: Any = None,
        content_ref: str | None = None,
        evidence_type: EvidenceType = EvidenceType.UNKNOWN,
        confidence: float = 0.0,
        execution_id: str | None = None,
        evidence_ids: list[str] | None = None,
        stamp_integrity: bool = True,
    ) -> Evidence:
        evidence = Evidence(
            evidence_id=new_id("ev"),
            source=source,
            source_type=source_type,
            timestamp=utc_now(),
            content=content,
            content_ref=content_ref,
            evidence_type=evidence_type,
            confidence=confidence,
            provenance=list(evidence_ids or []),
            execution_id=execution_id,
            integrity_hash=None,
        )
        self.contract.validate(evidence)
        if stamp_integrity:
            EvidenceIntegrity.stamp(evidence)
        await self.store.register(evidence)
        return evidence

    async def register_fact(
        self,
        *,
        source: str,
        source_type: str,
        content: Any = None,
        confidence: float = 1.0,
        execution_id: str | None = None,
        evidence_ids: list[str] | None = None,
    ) -> Evidence:
        return await self.register(
            source=source,
            source_type=source_type,
            content=content,
            evidence_type=EvidenceType.FACT,
            confidence=confidence,
            execution_id=execution_id,
            evidence_ids=evidence_ids,
        )

    async def register_inference(
        self,
        *,
        source: str,
        source_type: str = "agent",
        content: Any = None,
        confidence: float = 0.5,
        execution_id: str | None = None,
    ) -> Evidence:
        return await self.register(
            source=source,
            source_type=source_type,
            content=content,
            evidence_type=EvidenceType.INFERENCE,
            confidence=confidence,
            execution_id=execution_id,
        )

    async def register_unknown(
        self,
        *,
        source: str,
        source_type: str = "agent",
        content: Any = None,
        execution_id: str | None = None,
    ) -> Evidence:
        return await self.register(
            source=source,
            source_type=source_type,
            content=content,
            evidence_type=EvidenceType.UNKNOWN,
            confidence=0.0,
            execution_id=execution_id,
        )

    async def retrieve(self, evidence_id: str) -> Evidence | None:
        return await self.store.get(evidence_id)

    async def validate(self, evidence: Evidence) -> Evidence:
        """Validate an existing evidence record (e.g. after retrieval)."""
        try:
            return self.contract.validate(evidence)
        except EvidenceValidationError:
            raise

    async def verify_integrity(self, evidence: Evidence) -> bool:
        return EvidenceIntegrity.verify(evidence)

    async def assert_intact(self, evidence: Evidence) -> None:
        EvidenceIntegrity.assert_intact(evidence)

    async def promote_to_fact(self, evidence: Evidence) -> Evidence:
        """Explicit promotion. The contract never silently promotes
        UNKNOWN. INFERENCE may be promoted only if it meets FACT rules."""
        self.contract.promote_to_fact(evidence)
        EvidenceIntegrity.stamp(evidence)
        await self.store.register(evidence)
        return evidence

    @property
    def fact_min_confidence(self) -> float:
        return FACT_MIN_CONFIDENCE

    @property
    def fact_allowed_sources(self) -> frozenset[str]:
        return FACT_ALLOWED_SOURCES

    async def list_all(self, *, limit: int = 100) -> list[Evidence]:
        return await self.store.list_all(limit=limit)

    async def find_by_execution(self, execution_id: str) -> list[Evidence]:
        return await self.store.find_by_execution(execution_id)
