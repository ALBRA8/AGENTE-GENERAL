"""ProvenanceTracker — records provenance for the major seed artifacts.

Wraps a ProvenanceStore. Provides convenience builders for the canonical
record kinds:
    input | output | memory | evidence | tool_call | provider_call |
    decision | workflow
"""

from __future__ import annotations

from typing import Any

from albra_seed.core.types import Provenance, new_id, utc_now
from albra_seed.provenance.store import ProvenanceStore


class ProvenanceTracker:
    """Single entry point for provenance tracking across the seed."""

    KIND_INPUT = "input"
    KIND_OUTPUT = "output"
    KIND_MEMORY = "memory"
    KIND_EVIDENCE = "evidence"
    KIND_TOOL_CALL = "tool_call"
    KIND_PROVIDER_CALL = "provider_call"
    KIND_DECISION = "decision"
    KIND_WORKFLOW = "workflow"

    def __init__(self, store: ProvenanceStore | None = None) -> None:
        # Default to an in-memory store so CORE_ONLY mode works with zero deps.
        from albra_seed.provenance.store import InMemoryProvenanceStore

        self.store: ProvenanceStore = store or InMemoryProvenanceStore()

    async def track(
        self,
        *,
        source: str,
        source_type: str,
        record_kind: str,
        record_ref: str | None = None,
        confidence: float = 0.0,
        evidence_ids: list[str] | None = None,
        execution_id: str | None = None,
        correlation_id: str | None = None,
        parent_provenance_id: str | None = None,
    ) -> Provenance:
        prov = Provenance(
            provenance_id=new_id("prov"),
            source=source,
            source_type=source_type,
            timestamp=utc_now(),
            confidence=max(0.0, min(1.0, float(confidence))),
            evidence_ids=list(evidence_ids or []),
            execution_id=execution_id,
            correlation_id=correlation_id,
            parent_provenance_id=parent_provenance_id,
            version=1,
        )
        await self.store.record(
            prov, record_kind=record_kind, record_ref=record_ref
        )
        return prov

    # --- convenience builders -------------------------------------------

    async def track_input(
        self, *, source: str, record_ref: str | None = None, **kw: Any
    ) -> Provenance:
        return await self.track(
            source=source,
            source_type="user",
            record_kind=self.KIND_INPUT,
            record_ref=record_ref,
            **kw,
        )

    async def track_output(
        self, *, source: str, record_ref: str | None = None, **kw: Any
    ) -> Provenance:
        return await self.track(
            source=source,
            source_type="agent",
            record_kind=self.KIND_OUTPUT,
            record_ref=record_ref,
            **kw,
        )

    async def track_memory(
        self, *, source: str, record_ref: str | None = None, **kw: Any
    ) -> Provenance:
        return await self.track(
            source=source,
            source_type="agent",
            record_kind=self.KIND_MEMORY,
            record_ref=record_ref,
            **kw,
        )

    async def track_evidence(
        self, *, source: str, source_type: str, record_ref: str | None = None, **kw: Any
    ) -> Provenance:
        return await self.track(
            source=source,
            source_type=source_type,
            record_kind=self.KIND_EVIDENCE,
            record_ref=record_ref,
            **kw,
        )

    async def track_tool_call(
        self, *, tool_name: str, record_ref: str | None = None, **kw: Any
    ) -> Provenance:
        return await self.track(
            source=tool_name,
            source_type="tool",
            record_kind=self.KIND_TOOL_CALL,
            record_ref=record_ref,
            **kw,
        )

    async def track_provider_call(
        self, *, provider_name: str, record_ref: str | None = None, **kw: Any
    ) -> Provenance:
        return await self.track(
            source=provider_name,
            source_type="provider",
            record_kind=self.KIND_PROVIDER_CALL,
            record_ref=record_ref,
            **kw,
        )

    async def track_decision(
        self, *, decision: str, record_ref: str | None = None, **kw: Any
    ) -> Provenance:
        return await self.track(
            source=decision,
            source_type="agent",
            record_kind=self.KIND_DECISION,
            record_ref=record_ref,
            **kw,
        )

    async def track_workflow(
        self, *, workflow: str, record_ref: str | None = None, **kw: Any
    ) -> Provenance:
        return await self.track(
            source=workflow,
            source_type="workflow",
            record_kind=self.KIND_WORKFLOW,
            record_ref=record_ref,
            **kw,
        )

    # --- lookup helpers -------------------------------------------------

    async def for_record(self, record_kind: str, record_ref: str) -> list[Provenance]:
        return await self.store.find_by_ref(record_kind, record_ref)

    async def for_execution(self, execution_id: str) -> list[Provenance]:
        return await self.store.find_by_execution(execution_id)

    async def for_correlation(self, correlation_id: str) -> list[Provenance]:
        return await self.store.find_by_correlation(correlation_id)
