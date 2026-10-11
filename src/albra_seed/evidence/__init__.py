"""Evidence subpackage — formal evidence contract.

Public API:
    from albra_seed.evidence import (
        EvidenceManager,
        EvidenceStore,
        SQLiteEvidenceStore,
        InMemoryEvidenceStore,
        EvidenceContract,
        CitationGate,
        EvidenceIntegrity,
    )

Hard rules enforced here:
    - FACT requires confidence >= 0.9 AND source_type in {tool, external, verified_provider}
    - INFERENCE never auto-promoted to FACT
    - UNKNOWN never auto-promoted to FACT
"""

from albra_seed.evidence.citation import CitationGate
from albra_seed.evidence.contract import EvidenceContract, FACT_MIN_CONFIDENCE, FACT_ALLOWED_SOURCES
from albra_seed.evidence.integrity import EvidenceIntegrity
from albra_seed.evidence.manager import EvidenceManager
from albra_seed.evidence.store import (
    EvidenceStore,
    InMemoryEvidenceStore,
    SQLiteEvidenceStore,
)

__all__ = [
    "EvidenceManager",
    "EvidenceStore",
    "SQLiteEvidenceStore",
    "InMemoryEvidenceStore",
    "EvidenceContract",
    "FACT_MIN_CONFIDENCE",
    "FACT_ALLOWED_SOURCES",
    "CitationGate",
    "EvidenceIntegrity",
]
