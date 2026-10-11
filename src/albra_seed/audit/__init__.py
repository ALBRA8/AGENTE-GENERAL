"""Audit subpackage — NORMAL + TAMPER-EVIDENT audit logs.

Two distinct logs, NEVER conflated:

  NORMAL_AUDIT (`AuditLog`):
      * Structured JSONL records.
      * Append-only.
      * High-volume, easy to query (filter by correlation_id /
        execution_id / actor).
      * NOT tamper-evident — relies on file permissions / DB ACLs for
        integrity.
      * Use case: dashboards, SIEM ingestion, operator queries.

  TAMPER_EVIDENT_AUDIT (`TamperEvidentAuditLog`):
      * Hash-chained records (`prev_hash` + `record_hash`).
      * Append-only.
      * Tampering with any historical record invalidates every
        subsequent hash and is detectable via `verify_chain()` /
        `detect_tampering()`.
      * Use case: compliance, chain-of-custody, kill-switch events,
        approval decisions.

The two logs MAY be used simultaneously (and SHOULD be in production):
the normal log is convenient; the tamper-evident log is authoritative.

NO domain knowledge is introduced anywhere — the schema records only
universal audit fields (action, actor, tool, provider, permission,
approval, result, error, timestamp, correlation_id, metadata).
"""

from albra_seed.audit.entry import AuditEntry, TamperEvidentEntry
from albra_seed.audit.normal import AuditLog
from albra_seed.audit.tamper_evident import TamperEvidentAuditLog
from albra_seed.audit.store import (
    AuditStore,
    InMemoryAuditStore,
    FileAuditStore,
    SQLiteAuditStore,
)
from albra_seed.audit.verifier import (
    ChainVerifier,
    TamperEvidence,
    VerificationResult,
)

__all__ = [
    # NORMAL_AUDIT
    "AuditEntry",
    "AuditLog",
    "AuditStore",
    "InMemoryAuditStore",
    "FileAuditStore",
    "SQLiteAuditStore",
    # TAMPER_EVIDENT_AUDIT
    "TamperEvidentEntry",
    "TamperEvidentAuditLog",
    "ChainVerifier",
    "VerificationResult",
    "TamperEvidence",
]
