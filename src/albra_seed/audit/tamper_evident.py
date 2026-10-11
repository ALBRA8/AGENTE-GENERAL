"""TamperEvidentAuditLog — SEPARATE hash-chained audit log.

This is NOT the same as `AuditLog` (normal). It is a *separate* log
with a *separate* store, recording entries that are cryptographically
hash-chained. Any tampering with a historical record invalidates every
subsequent hash.

Use cases:
  * Compliance / chain-of-custody records.
  * Recording approval decisions, permission grants, kill-switch events.
  * Anything where "did this happen, exactly as recorded?" matters.

The log enforces:
  * Append-only (no update / delete API).
  * Hash-chain integrity (each record's `prev_hash` = previous record's
    `record_hash`).
  * Genesis hash = SHA-256("") — a well-known constant.
"""

from __future__ import annotations

from typing import Any

from albra_seed.audit.entry import TamperEvidentEntry
from albra_seed.audit.verifier import (
    ChainVerifier,
    TamperEvidence,
    VerificationResult,
)
from albra_seed.context.correlation import CorrelationTracker


class _InMemoryTEStore:
    """Simple in-memory store for tamper-evident entries."""

    def __init__(self) -> None:
        self._entries: list[TamperEvidentEntry] = []

    def append(self, entry: TamperEvidentEntry) -> None:
        self._entries.append(entry)

    def all(self) -> list[TamperEvidentEntry]:
        return list(self._entries)

    def head_hash(self) -> str:
        if not self._entries:
            return ChainVerifier.GENESIS
        return self._entries[-1].record_hash

    def count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()


class TamperEvidentAuditLog:
    """Hash-chained audit log. SEPARATE from `AuditLog` (normal).

    The chain root is `ChainVerifier.GENESIS` (SHA-256 of the empty
    string). Each `append` returns the new `record_hash`.

    Tampering with any record's content (action, actor, etc.) OR any
    record's `prev_hash` is detectable by `verify_chain()` and listed
    in detail by `detect_tampering()`.
    """

    def __init__(self, store: _InMemoryTEStore | None = None) -> None:
        self._store = store or _InMemoryTEStore()
        self._verifier = ChainVerifier()

    @property
    def head_hash(self) -> str:
        return self._store.head_hash()

    def append(
        self,
        record: dict[str, Any] | TamperEvidentEntry,
    ) -> str:
        """Append a record to the chain. Returns the new record_hash.

        `record` may be either a `TamperEvidentEntry` instance or a
        dict of fields. The prev_hash + record_hash are computed and
        set automatically.
        """
        if isinstance(record, TamperEvidentEntry):
            entry = record
        else:
            # Auto-fill correlation_id from context if missing.
            if "correlation_id" not in record or record["correlation_id"] is None:
                record["correlation_id"] = CorrelationTracker.current_correlation_id()
            if "execution_id" not in record or record["execution_id"] is None:
                record["execution_id"] = CorrelationTracker.current_execution_id()
            entry = TamperEvidentEntry(**record)
        prev_hash = self._store.head_hash()
        entry.prev_hash = prev_hash
        entry.record_hash = entry.compute_record_hash(prev_hash)
        self._store.append(entry)
        return entry.record_hash

    def all(self) -> list[TamperEvidentEntry]:
        return self._store.all()

    def verify_chain(self) -> VerificationResult:
        """Walk the chain; return a `VerificationResult`."""
        return self._verifier.verify(self._store.all())

    def detect_tampering(self) -> list[TamperEvidence]:
        """Return a list of all detected tamper evidences (may be empty)."""
        return self._verifier.detect_tampering(self._store.all())

    def count(self) -> int:
        return self._store.count()

    def clear(self) -> None:
        """Reset the chain. SHOULD only be called in tests — clearing a
        production audit log defeats the purpose of tamper-evidence."""
        self._store.clear()


__all__ = ["TamperEvidentAuditLog"]
