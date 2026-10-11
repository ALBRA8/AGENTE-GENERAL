"""MemoryDV — universal memory subsystem.

Five memory types: episodic / semantic / factual / procedural / working.
Plus DECISION (added non-destructively by GAP-3) which stores structured
decision records as MemoryRecord(memory_type=DECISION) via the
DecisionMemory adapter (sibling of MemoryManager — same store, same rules).
Strict isolation by (agent_id, domain, user_id, context_id).
All factual memory requires provenance + evidence.
UNKNOWN evidence type NEVER auto-promotes to FACT.

Public API:
    from albra_seed.memory import (
        MemoryManager,
        SQLiteMemoryStore,
        InMemoryMemoryStore,
        MemoryProvenance,
        MemoryIsolator,
        ConsolidationPolicy,
        MemoryLifecycle,
        DecisionRecord,
        DecisionMemory,
    )
"""

from albra_seed.memory.consolidation import ConsolidationPolicy
from albra_seed.memory.decision_store import DecisionMemory
from albra_seed.memory.decisions import DecisionRecord
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.memory.isolator import MemoryIsolator
from albra_seed.memory.lifecycle import MemoryLifecycle, MemoryLifecycleState
from albra_seed.memory.manager import MemoryManager
from albra_seed.memory.provenance import MemoryProvenance
from albra_seed.memory.sqlite_store import SQLiteMemoryStore

__all__ = [
    "MemoryManager",
    "SQLiteMemoryStore",
    "InMemoryMemoryStore",
    "MemoryProvenance",
    "MemoryIsolator",
    "ConsolidationPolicy",
    "MemoryLifecycle",
    "MemoryLifecycleState",
    "DecisionRecord",
    "DecisionMemory",
]
