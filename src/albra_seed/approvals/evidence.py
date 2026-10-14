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
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
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

class SQLiteApprovalEvidenceStore:
    """Durable, process-safe hash-chained approval evidence in SQLite.

    The chain is shared by every worker using the same database. Appends use
    a write transaction so concurrent writers cannot fork the chain.
    """

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.db_path = str(db_path)
        if self.db_path == ":memory:":
            raise ValueError("SQLiteApprovalEvidenceStore requires a file-backed database")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approval_evidence (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    evidence_id TEXT NOT NULL UNIQUE,
                    approval_id TEXT NOT NULL,
                    record_hash TEXT NOT NULL,
                    record_json TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_evidence_approval "
                "ON approval_evidence(approval_id, sequence)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path, timeout=self.busy_timeout_ms / 1000, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        return conn

    @property
    def head_hash(self) -> str:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT record_hash FROM approval_evidence ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        return row["record_hash"] if row else ApprovalEvidenceStore.GENESIS_HASH

    def append(self, record: ApprovalEvidence) -> ApprovalEvidence:
        """Atomically append evidence to the one durable chain."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT record_hash FROM approval_evidence ORDER BY sequence DESC LIMIT 1"
                ).fetchone()
                record.prev_hash = row["record_hash"] if row else ApprovalEvidenceStore.GENESIS_HASH
                record.record_hash = record.compute_hash()
                conn.execute(
                    "INSERT INTO approval_evidence"
                    "(evidence_id,approval_id,record_hash,record_json) VALUES(?,?,?,?)",
                    (record.evidence_id, record.approval_id, record.record_hash, record.model_dump_json()),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return record

    def list_for(self, approval_id: str) -> list[ApprovalEvidence]:
        return [r for r in self.all_records() if r.approval_id == approval_id]

    def all_records(self) -> list[ApprovalEvidence]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT record_json FROM approval_evidence ORDER BY sequence"
            ).fetchall()
        return [ApprovalEvidence.model_validate_json(row["record_json"]) for row in rows]

    def verify_chain(self) -> bool:
        previous = ApprovalEvidenceStore.GENESIS_HASH
        for record in self.all_records():
            if not record.verify(previous):
                return False
            previous = record.record_hash
        return True


__all__ = ["ApprovalEvidence", "ApprovalEvidenceStore", "SQLiteApprovalEvidenceStore"]
