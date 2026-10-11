"""ChainVerifier — walks the tamper-evident chain and detects tampering.

Returns a `VerificationResult` describing the first broken link (if any).
Does NOT mutate the chain. Used both as a passive audit tool and as
the engine behind `TamperEvidentAuditLog.verify_chain()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from albra_seed.audit.entry import TamperEvidentEntry


@dataclass
class VerificationResult:
    """Result of verifying a tamper-evident audit chain.

    `valid` is True iff every link's `prev_hash` matches its predecessor's
    `record_hash` AND every `record_hash` matches the computed hash for
    the entry's content + prev_hash.

    `broken_at` is the 0-based index of the first broken link (None if
    valid). `expected_hash` is what the prev_hash SHOULD have been;
    `actual_hash` is what it WAS (only meaningful if `kind` is
    `prev_hash_mismatch`).
    """

    valid: bool
    broken_at: int | None = None
    kind: str | None = None  # "prev_hash_mismatch" | "record_hash_mismatch" | None
    expected_hash: str | None = None
    actual_hash: str | None = None
    chain_length: int = 0
    details: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "broken_at": self.broken_at,
            "kind": self.kind,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "chain_length": self.chain_length,
            "details": self.details,
        }


@dataclass
class TamperEvidence:
    """Description of a single tamper occurrence in the chain."""

    index: int
    kind: str  # "prev_hash_mismatch" | "record_hash_mismatch"
    expected: str | None
    actual: str | None
    entry_id: str | None = None
    details: str | None = None


class ChainVerifier:
    """Stateless verifier — pass a list of entries, get a result."""

    GENESIS_HASH = "0" * 64  # SHA-256 of empty string is 64 zeros? No — used as sentinel.

    # Actually we use the SHA-256 of the empty string as the genesis.
    GENESIS = __import__("hashlib").sha256(b"").hexdigest()

    def verify(self, entries: list[TamperEvidentEntry]) -> VerificationResult:
        """Walk the chain; return the first broken link (or all-valid)."""
        prev = self.GENESIS
        for i, entry in enumerate(entries):
            if entry.prev_hash != prev:
                return VerificationResult(
                    valid=False,
                    broken_at=i,
                    kind="prev_hash_mismatch",
                    expected_hash=prev,
                    actual_hash=entry.prev_hash,
                    chain_length=len(entries),
                    details=f"prev_hash at index {i} does not match predecessor's record_hash",
                )
            computed = entry.compute_record_hash(prev)
            if entry.record_hash != computed:
                return VerificationResult(
                    valid=False,
                    broken_at=i,
                    kind="record_hash_mismatch",
                    expected_hash=computed,
                    actual_hash=entry.record_hash,
                    chain_length=len(entries),
                    details=f"record_hash at index {i} does not match computed hash",
                )
            prev = entry.record_hash
        return VerificationResult(
            valid=True,
            broken_at=None,
            kind=None,
            expected_hash=None,
            actual_hash=None,
            chain_length=len(entries),
            details="chain intact",
        )

    def detect_tampering(self, entries: list[TamperEvidentEntry]) -> list[TamperEvidence]:
        """Return ALL broken links (not just the first)."""
        out: list[TamperEvidence] = []
        prev = self.GENESIS
        for i, entry in enumerate(entries):
            if entry.prev_hash != prev:
                out.append(TamperEvidence(
                    index=i,
                    kind="prev_hash_mismatch",
                    expected=prev,
                    actual=entry.prev_hash,
                    entry_id=entry.entry_id,
                ))
                # Don't continue walking from here — chain is broken.
                # Use the entry's own record_hash as the new "prev" so we
                # can still detect further tampering downstream.
                prev = entry.record_hash
                continue
            computed = entry.compute_record_hash(prev)
            if entry.record_hash != computed:
                out.append(TamperEvidence(
                    index=i,
                    kind="record_hash_mismatch",
                    expected=computed,
                    actual=entry.record_hash,
                    entry_id=entry.entry_id,
                ))
            prev = entry.record_hash
        return out


__all__ = ["VerificationResult", "TamperEvidence", "ChainVerifier"]
