"""Evidence integrity — SHA-256 hashing for tamper detection.

Computes a stable hash over canonical JSON of the evidence fields. Detects
tampering with content, source, confidence, evidence_type, or provenance.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from albra_seed.core.types import Evidence


class EvidenceIntegrity:
    """Computes and verifies SHA-256 integrity hashes for Evidence records."""

    @staticmethod
    def _canonical(evidence: Evidence) -> str:
        payload: dict[str, Any] = {
            "evidence_id": evidence.evidence_id,
            "source": evidence.source,
            "source_type": evidence.source_type,
            "content": evidence.content,
            "content_ref": evidence.content_ref,
            "evidence_type": evidence.evidence_type.value
            if hasattr(evidence.evidence_type, "value")
            else str(evidence.evidence_type),
            "confidence": float(evidence.confidence),
            "provenance": sorted(list(evidence.provenance or [])),
            "execution_id": evidence.execution_id,
        }
        return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))

    @classmethod
    def compute_hash(cls, evidence: Evidence) -> str:
        canonical = cls._canonical(evidence)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def stamp(cls, evidence: Evidence) -> Evidence:
        evidence.integrity_hash = cls.compute_hash(evidence)
        return evidence

    @classmethod
    def verify(cls, evidence: Evidence) -> bool:
        """Returns True iff the stored integrity_hash matches the recomputed hash."""
        if not evidence.integrity_hash:
            return False
        recomputed = cls.compute_hash(evidence)
        # constant-time compare
        if len(recomputed) != len(evidence.integrity_hash):
            return False
        result = 0
        for a, b in zip(recomputed, evidence.integrity_hash):
            result |= ord(a) ^ ord(b)
        return result == 0

    @classmethod
    def assert_intact(cls, evidence: Evidence) -> None:
        if not cls.verify(evidence):
            raise ValueError(
                f"evidence integrity violation: hash mismatch for {evidence.evidence_id}"
            )

    @classmethod
    def detect_tampering(cls, evidence: Evidence) -> bool:
        """Returns True iff the evidence has been tampered with."""
        return not cls.verify(evidence)
