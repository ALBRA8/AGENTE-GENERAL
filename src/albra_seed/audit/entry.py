"""Audit entry models — NORMAL vs TAMPER_EVIDENT.

Two distinct entry kinds, NEVER conflated:

  * AuditEntry              — NORMAL audit record. Append-only JSONL row.
                              Records *what happened* (execution, action,
                              actor, tool, provider, permission, approval,
                              result, error, timestamp, correlation_id).

  * TamperEvidentEntry     — TAMPER_EVIDENT audit record. Extends
                              AuditEntry with `prev_hash` + `record_hash`.
                              Hash-chained so any tampering is detectable.

The two kinds are deliberately SEPARATE classes (not a flag on one
class) to make it impossible to accidentally include `prev_hash` /
`record_hash` on a normal audit entry, or to forget them on a tamper-
evident entry.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now


def _canonical_json(obj: dict[str, Any]) -> str:
    """Stable JSON serialization for hashing — sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))


def _sha256(payload: str | dict[str, Any]) -> str:
    if isinstance(payload, dict):
        payload = _canonical_json(payload)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# =====================================================================
# NORMAL audit entry
# =====================================================================


class AuditEntry(BaseModel):
    """NORMAL audit entry — append-only, no hash chain.

    Records the universal fields required by every audit event. Domain-
    specific extensions should live in `metadata` to keep the core
    schema stable.
    """

    entry_id: str = Field(default_factory=lambda: new_id("aud"))
    timestamp: datetime = Field(default_factory=utc_now)
    correlation_id: str | None = None
    execution_id: str | None = None
    actor: str | None = None
    action: str
    tool: str | None = None
    provider: str | None = None
    permission: str | None = None
    approval_id: str | None = None
    result: str = "success"  # success | failure | denied | error | pending
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        # Pydantic serializes datetime to ISO via mode="json"
        return d

    def to_jsonl(self) -> str:
        """One-line JSON for JSONL files."""
        return json.dumps(self.to_dict(), default=str, sort_keys=True)


# =====================================================================
# TAMPER-EVIDENT audit entry
# =====================================================================


class TamperEvidentEntry(BaseModel):
    """TAMPER-EVIDENT audit entry — hash-chained, append-only.

    Each entry carries:
      * `prev_hash`     — SHA-256 of the previous entry's `record_hash`
                          (or the GENESIS hash for the first entry).
      * `record_hash`    — SHA-256 of `prev_hash + canonical_json(content)`
                            where content excludes `prev_hash` and
                            `record_hash` themselves.

    Tampering with any field of any entry invalidates that entry's
    `record_hash` AND every subsequent entry's `prev_hash` — providing
    cryptographic tamper-EVIDENCE (not authentication; signatures
    require key material intentionally out of scope for the seed).
    """

    entry_id: str = Field(default_factory=lambda: new_id("teaud"))
    timestamp: datetime = Field(default_factory=utc_now)
    correlation_id: str | None = None
    execution_id: str | None = None
    actor: str | None = None
    action: str
    tool: str | None = None
    provider: str | None = None
    permission: str | None = None
    approval_id: str | None = None
    result: str = "success"
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Hash-chain fields:
    prev_hash: str = ""
    record_hash: str = ""

    def content_payload(self) -> dict[str, Any]:
        """Return the entry WITHOUT the hash fields — used for hashing."""
        d = self.model_dump(mode="json", exclude={"prev_hash", "record_hash"})
        return d

    def compute_record_hash(self, prev_hash: str | None = None) -> str:
        """Compute the canonical hash for this entry given prev_hash.

        `prev_hash` argument takes precedence over `self.prev_hash` so
        callers can compute "what hash SHOULD this entry have if its
        prev_hash were X" without mutating the entry.
        """
        ph = prev_hash if prev_hash is not None else self.prev_hash
        content = self.content_payload()
        # Concatenate prev_hash + canonical_json(content). Order matters
        # for hash stability — we use string concatenation with a
        # separator to avoid ambiguity.
        payload = ph + "|" + _canonical_json(content)
        return _sha256(payload)

    def verify(self, expected_prev_hash: str) -> bool:
        """True iff `prev_hash` matches expected AND `record_hash` matches
        the computed hash."""
        if self.prev_hash != expected_prev_hash:
            return False
        return self.record_hash == self.compute_record_hash()

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), default=str, sort_keys=True)


__all__ = [
    "AuditEntry",
    "TamperEvidentEntry",
    "_canonical_json",
    "_sha256",
]
