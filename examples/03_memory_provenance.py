#!/usr/bin/env python3
"""03_memory_provenance.py — Write memory with provenance, recall it.

Demonstrates MemoryDV:
    1. InMemoryMemoryStore + MemoryManager.
    2. Writing an EPISODIC memory (no evidence required).
    3. Writing a FACTUAL memory (requires evidence_ids + qualifying source_type).
    4. Recalling memories within an isolation bucket.
    5. Verifying memory isolation across (agent_id, domain, user_id, context_id).

Run:
    python examples/03_memory_provenance.py
"""

from __future__ import annotations

import asyncio

from albra_seed import MemoryType
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.memory.manager import MemoryManager


async def main() -> None:
    mgr = MemoryManager(store=InMemoryMemoryStore())

    # 1. EPISODIC memory — no evidence required; just records what happened.
    ep = await mgr.remember(
        agent_id="agent-A", domain="default", user_id="u1", context_id="case-1",
        content={"event": "user asked about X", "outcome": "answered"},
        memory_type=MemoryType.EPISODIC,
        source="agent", source_type="agent", confidence=0.5,
    )
    print(f"episodic record_id   : {ep.record_id}")
    print(f"  provenance_id      : {ep.provenance_id}")
    print(f"  is_fact            : {ep.is_fact}")

    # 2. FACTUAL memory — REQUIRES evidence_ids + qualifying source_type.
    fact = await mgr.remember(
        agent_id="agent-A", domain="default", user_id="u1", context_id="case-1",
        content={"rule": "mock rule"},
        memory_type=MemoryType.FACTUAL,
        source="external_db", source_type="external", confidence=0.95,
        evidence_ids=["ev_001", "ev_002"],   # ← required for is_fact=True
        is_fact=True,
    )
    print(f"factual record_id    : {fact.record_id}")
    print(f"  is_fact            : {fact.is_fact}")
    print(f"  evidence_ids       : {fact.evidence_ids}")

    # 3. Recall within the same isolation bucket.
    results = await mgr.recall(
        agent_id="agent-A", domain="default", user_id="u1", context_id="case-1",
    )
    print(f"recalled in bucket  : {len(results)} record(s)")

    # 4. Recall from a DIFFERENT bucket → must be empty (isolation enforced).
    other = await mgr.recall(
        agent_id="agent-A", domain="default", user_id="u2", context_id="case-1",
    )
    print(f"recalled in other   : {len(other)} record(s) (isolation: {len(other) == 0})")


if __name__ == "__main__":
    asyncio.run(main())
