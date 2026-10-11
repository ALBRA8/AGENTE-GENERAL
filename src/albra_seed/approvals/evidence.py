"""ApprovalEvidence — tamper-evident record of approval decisions.

Every approval decision (APPROVED, REJECTED, EXPIRED, ESCALATED) is
recorded as an immutable, hash-chained `ApprovalEvidence` record. The
chain is forward-linked: each new record's `prev_hash` is the SHA-256
of the previous record's canonical JSON.

Tampering with any historical record invalidates every subsequent hash
in the chain — providing cryptographic tamper-evidence (not crypto-
graphic authentication — that requires signature keys, intentionally
out of scope for the seed).

The seed ships an in-memory chain. Specialization layers may back this
with a durable store (DB / append-only log / blockchain).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import ApprovalStatus, new_id, utc_now


def _canonical_json(obj: dict[str, Any]) -> str:
    """Stable JSON serialization for hashing."""
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class ApprovalEvidence(BaseModel):
    """A single tamper-evident record of an approval decision."""

    evidence_id: str = Field(default_factory=lambda: new_id("appev"))
    approval_id: str
    status: ApprovalStatus
    decided_by: str | None = None
    decided_at: datetime = Field(default_factory=utc_now)
    reason: str = ""
    prev_hash: str = ""  # hash of the previous record in the chain
    record_hash: str = ""  # hash of THIS record (excluding record_hash itself)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def compute_hash(self) -> str:
        """Compute the SHA-256 hash of this record (excluding record_hash).

        Used by `ApprovalEvidenceStore.append` to set `record_hash`.
        """
        payload = self.model_dump(exclude={"record_hash"})
        # Convert datetimes to ISO for stable hashing.
        payload["decided_at"] = (
            self.decided_at.isoformat() if self.decided_at else None
        )
        return _hash(payload)

    def verify(self, prev_hash: str) -> bool:
        """True iff this record's `prev_hash` matches AND `record_hash`
        matches the computed hash."""
        if self.prev_hash != prev_hash:
            return False
        return self.record_hash == self.compute_hash()


class ApprovalEvidenceStore:
    """In-memory hash-chained evidence store.

    The chain root is the SHA-256 of the empty string (well-known
    genesis). Every append returns the new record's `record_hash`.
    `verify_chain()` walks the chain and returns True iff every link
    is intact.
    """

    GENESIS_HASH = hashlib.sha256(b"").hexdigest()

    def __init__(self) -> None:
        self._records: list[ApprovalEvidence] = []

    @property
    def head_hash(self) -> str:
        if not self._records:
            return self.GENESIS_HASH
        return self._records[-1].record_hash

    def append(self, record: ApprovalEvidence) -> ApprovalEvidence:
        record.prev_hash = self.head_hash
        record.record_hash = record.compute_hash()
        self._records.append(record)
        return record

    def list_for(self, approval_id: str) -> list[ApprovalEvidence]:
        return [r for r in self._records if r.approval_id == approval_id]

    def all_records(self) -> list[ApprovalEvidence]:
        return list(self._records)

    def verify_chain(self) -> bool:
        prev = self.GENESIS_HASH
        for r in self._records:
            if not r.verify(prev):
                return False
            prev = r.record_hash
        return True


__all__ = ["ApprovalEvidence", "ApprovalEvidenceStore"]
