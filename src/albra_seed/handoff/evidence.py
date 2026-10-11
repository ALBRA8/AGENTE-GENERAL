"""Handoff evidence — tamper-evident record of a handoff.

Evidence is hash-chained over canonical JSON of (request, response,
previous hash). Tampering with any field invalidates the chain from that
point onward.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import utc_now
from albra_seed.handoff.contract import HandoffRequest, HandoffResponse


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON encoding for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class HandoffEvidence(BaseModel):
    """Tamper-evident record of a single handoff transaction.

    `record_hash` covers the canonical JSON of (request, response,
    prev_hash, captured_at). The chain head is stored in
    `HandoffEvidenceChain`.
    """

    evidence_id: str
    execution_id: str
    requester: str
    target: str
    request_snapshot: dict[str, Any]
    response_snapshot: dict[str, Any]
    status: str
    captured_at: datetime = Field(default_factory=utc_now)
    prev_hash: str = ""
    record_hash: str = ""

    def compute_hash(self) -> str:
        payload = _canonical_json(
            {
                "evidence_id": self.evidence_id,
                "execution_id": self.execution_id,
                "requester": self.requester,
                "target": self.target,
                "request_snapshot": self.request_snapshot,
                "response_snapshot": self.response_snapshot,
                "status": self.status,
                "captured_at": self.captured_at.isoformat(),
                "prev_hash": self.prev_hash,
            }
        )
        return _hash(payload)

    def seal(self, prev_hash: str = "") -> str:
        self.prev_hash = prev_hash
        self.record_hash = self.compute_hash()
        return self.record_hash

    def verify(self) -> bool:
        if not self.record_hash:
            return False
        return self.compute_hash() == self.record_hash


class HandoffEvidenceChain:
    """Append-only chain of `HandoffEvidence` records."""

    def __init__(self) -> None:
        self._entries: list[HandoffEvidence] = []

    def append(
        self,
        *,
        evidence_id: str,
        request: HandoffRequest,
        response: HandoffResponse,
    ) -> HandoffEvidence:
        prev_hash = self._entries[-1].record_hash if self._entries else ""
        entry = HandoffEvidence(
            evidence_id=evidence_id,
            execution_id=request.execution_id,
            requester=request.requester,
            target=request.target,
            request_snapshot=request.model_dump(mode="json"),
            response_snapshot=response.model_dump(mode="json"),
            status=response.status,
        )
        entry.seal(prev_hash=prev_hash)
        self._entries.append(entry)
        return entry

    def verify_chain(self) -> bool:
        prev = ""
        for entry in self._entries:
            if entry.prev_hash != prev:
                return False
            if not entry.verify():
                return False
            prev = entry.record_hash
        return True

    def entries(self) -> list[HandoffEvidence]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def build_evidence(
    request: HandoffRequest,
    response: HandoffResponse,
    evidence_id: str | None = None,
) -> HandoffEvidence:
    """Convenience constructor for a standalone (non-chained) record."""
    from albra_seed.core.types import new_id

    entry = HandoffEvidence(
        evidence_id=evidence_id or new_id("h-ev"),
        execution_id=request.execution_id,
        requester=request.requester,
        target=request.target,
        request_snapshot=request.model_dump(mode="json"),
        response_snapshot=response.model_dump(mode="json"),
        status=response.status,
    )
    entry.seal(prev_hash="")
    return entry


__all__ = [
    "HandoffEvidence",
    "HandoffEvidenceChain",
    "build_evidence",
]
