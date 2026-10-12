"""P1-B — retrieval at scale tests.

Verifies that ``MemoryRetriever.retrieve()`` correctly finds OLD-but-relevant
memories when the store contains 1500+ records, that the candidate pool is
large enough to include them, and that ranking / isolation / lifecycle
behaviour is preserved at scale.

Pre-fix regression:
  - ``retrieve()`` called ``manager.recall(limit or 100)``. With 1500
    records, the candidate pool was the 100 newest (SQLite) or the 100
    oldest by insertion (InMemory). An OLD relevant memory was excluded
    from the candidate pool before ranking, so it could NEVER be retrieved
    no matter how well it matched the query.

Post-fix:
  - ``RetrievalConfig.candidate_limit`` (default 1000, 10x the previous
    hardcoded 100) bounds the candidate pool. Callers can override via
    the config or an explicit ``limit=`` argument.
  - InMemory store now sorts by ``created_at DESC`` (consistent with
    SQLite) so both backends return the same SET of candidates for the
    same query + limit, instead of InMemory silently returning
    insertion-order records.

Coverage:
    1. test_retrieval_finds_old_relevant_memory_amid_1500
    2. test_retrieval_with_distractors
    3. test_retrieval_lexical_partial_match
    4. test_retrieval_excludes_dead_records_at_scale
    5. test_retrieval_isolation_at_scale
    6. test_retrieval_inmemory_vs_sqlite_consistency
    7. test_retrieval_with_varied_confidence_and_provenance

CRITICAL: every test runs with NO external credentials. All async — pytest
is configured with ``asyncio_mode = "auto"``.
"""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta

from albra_seed.core.base import MemoryRecord
from albra_seed.core.types import MemoryType, utc_now
from albra_seed.memory import (
    InMemoryMemoryStore,
    MemoryManager,
    MemoryRetriever,
    RetrievalConfig,
    SQLiteMemoryStore,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_record(
    *,
    record_id: str,
    agent_id: str = "agent-A",
    domain: str = "default",
    user_id: str | None = None,
    context_id: str | None = None,
    execution_id: str | None = None,
    memory_type: MemoryType = MemoryType.SEMANTIC,
    content: str = "scratch",
    confidence: float = 0.5,
    source: str = "agent",
    source_type: str = "agent",
    evidence_ids: list[str] | None = None,
    is_fact: bool = False,
    created_at=None,
) -> MemoryRecord:
    """Build a MemoryRecord directly — bypasses remember() provenance/
    lifecycle overhead so we can write 1500+ records fast and with
    deterministic ``created_at`` timestamps.

    The retriever's ``_is_alive()`` returns True for records with no
    lifecycle history (no dead events → alive), so direct store writes
    are safe for retrieval tests.
    """
    ts = created_at if created_at is not None else utc_now()
    return MemoryRecord(
        record_id=record_id,
        agent_id=agent_id,
        domain=domain,
        user_id=user_id,
        context_id=context_id,
        execution_id=execution_id,
        memory_type=memory_type,
        content=content,
        confidence=confidence,
        source=source,
        source_type=source_type,
        evidence_ids=evidence_ids or [],
        is_fact=is_fact,
        created_at=ts,
        modified_at=ts,
    )


async def _write_many(store, records):
    for r in records:
        await store.write(r)


# --------------------------------------------------------------------------- #
# 1. retrieval finds an OLD relevant memory amid 1500 recent irrelevant ones
# --------------------------------------------------------------------------- #


async def test_retrieval_finds_old_relevant_memory_amid_1500() -> None:
    """Write 1499 recent irrelevant memories + 1 OLD relevant memory (30
    days old, matching the query). With ``candidate_limit=2000`` the
    retriever pulls ALL 1500 candidates and ranks the OLD relevant memory
    into the top_k.

    Pre-fix: ``limit=100`` meant only the 100 NEWEST records were pulled.
    The OLD relevant memory was NEVER in the candidate pool — it could
    not be retrieved no matter how well it matched.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    # Override candidate_limit so the candidate pool covers all 1500 records.
    config = RetrievalConfig(candidate_limit=2000, top_k=10, min_score=0.05)
    retriever = MemoryRetriever(mgr, config=config)

    base = utc_now()
    # 1499 recent irrelevant records — content has no overlap with the query.
    noise = [
        _make_record(
            record_id=f"noise-{i}",
            content=f"completely unrelated noise record number {i}",
            memory_type=MemoryType.SEMANTIC,
            confidence=0.5,
            created_at=base + timedelta(seconds=i),
        )
        for i in range(1499)
    ]
    # 1 OLD relevant memory — 30 days old, contains the query terms.
    old_relevant = _make_record(
        record_id="old-relevant",
        content="deploy kubernetes production checklist with helm charts",
        memory_type=MemoryType.SEMANTIC,
        confidence=0.5,
        created_at=base - timedelta(days=30),
    )
    await _write_many(store, noise + [old_relevant])

    results = await retriever.retrieve(
        agent_id="agent-A", query="deploy kubernetes production"
    )
    retrieved_ids = {rm.record.record_id for rm in results}

    # The OLD relevant memory MUST be in the top_k results.
    assert "old-relevant" in retrieved_ids, (
        "OLD relevant memory was not retrieved — the candidate_limit is "
        "still too small or the InMemory store is returning insertion-order "
        "records that exclude the old memory from the candidate pool"
    )


# --------------------------------------------------------------------------- #
# 2. retrieval surfaces all matching memories from 1000 distractors
# --------------------------------------------------------------------------- #


async def test_retrieval_with_distractors() -> None:
    """Write 1000 memories with various content; 5 of them match the query.
    Assert all 5 are retrieved (top 5 by score)."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    config = RetrievalConfig(candidate_limit=2000, top_k=10, min_score=0.05)
    retriever = MemoryRetriever(mgr, config=config)

    base = utc_now()
    # 995 distractors.
    distractors = [
        _make_record(
            record_id=f"distractor-{i}",
            content=f"random topic number {i} unrelated to query",
            memory_type=MemoryType.SEMANTIC,
            confidence=0.5,
            created_at=base + timedelta(seconds=i),
        )
        for i in range(995)
    ]
    # 5 matching records — all contain the query terms.
    matching_ids = {f"match-{i}" for i in range(5)}
    matching = [
        _make_record(
            record_id=rid,
            content="python testing framework for unit integration coverage",
            memory_type=MemoryType.SEMANTIC,
            confidence=0.5,
            created_at=base + timedelta(seconds=1000 + i),
        )
        for i, rid in enumerate(sorted(matching_ids))
    ]
    await _write_many(store, distractors + matching)

    results = await retriever.retrieve(
        agent_id="agent-A", query="python testing framework"
    )
    retrieved_ids = {rm.record.record_id for rm in results}

    # All 5 matching records are retrieved.
    assert matching_ids.issubset(retrieved_ids), (
        f"expected all 5 matching records in results, got "
        f"{retrieved_ids & matching_ids} of 5"
    )


# --------------------------------------------------------------------------- #
# 3. lexical partial match (query subset of content)
# --------------------------------------------------------------------------- #


async def test_retrieval_lexical_partial_match() -> None:
    """Query 'python testing' matches a memory whose content is 'python
    testing framework' — the query terms are a subset of the content,
    not an exact phrase match. The retriever tokenizes both and counts
    overlap; partial match is enough to score the memory above the
    min_score cutoff."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    retriever = MemoryRetriever(mgr)

    base = utc_now()
    await store.write(
        _make_record(
            record_id="partial",
            content="python testing framework covers unit integration and e2e",
            memory_type=MemoryType.SEMANTIC,
            confidence=0.5,
            created_at=base,
        )
    )

    results = await retriever.retrieve(
        agent_id="agent-A", query="python testing"
    )
    assert len(results) >= 1
    assert results[0].record.record_id == "partial"
    # The lexical breakdown should be 1.0 (both query terms found).
    assert results[0].score_breakdown["lexical"] == 1.0


# --------------------------------------------------------------------------- #
# 4. retrieval excludes dead records at scale
# --------------------------------------------------------------------------- #


async def test_retrieval_excludes_dead_records_at_scale() -> None:
    """Write 1500 records, mark 500 as DELETED via the lifecycle. Assert
    NONE of the 500 dead records appear in the retrieval results."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    config = RetrievalConfig(candidate_limit=2000, top_k=50, min_score=0.0)
    retriever = MemoryRetriever(mgr, config=config)

    base = utc_now()
    records = []
    dead_ids: set[str] = set()
    for i in range(1500):
        rid = f"rec-{i}"
        records.append(
            _make_record(
                record_id=rid,
                # All records match the query so all would otherwise be
                # retrieved — the lifecycle filter is the only thing keeping
                # the dead 500 out.
                content="python testing framework topic",
                memory_type=MemoryType.SEMANTIC,
                confidence=0.5,
                created_at=base + timedelta(seconds=i),
            )
        )
        if i < 500:
            dead_ids.add(rid)
    await _write_many(store, records)

    # Mark the first 500 as DELETED via the manager's lifecycle (without
    # removing them from the store — isolates the retriever's lifecycle
    # check from the store's tombstone behaviour).
    for rid in dead_ids:
        mgr.lifecycle.record_deleted(rid, reason="test-dead")

    results = await retriever.retrieve(
        agent_id="agent-A", query="python testing"
    )
    retrieved_ids = {rm.record.record_id for rm in results}

    # No dead records in results.
    assert not (retrieved_ids & dead_ids), (
        f"dead records leaked into retrieval results: "
        f"{retrieved_ids & dead_ids}"
    )
    # The 1000 live records are all retrieved (top_k=50, but we just need
    # to confirm NO dead ones slipped in).
    assert retrieved_ids.issubset({f"rec-{i}" for i in range(500, 1500)})


# --------------------------------------------------------------------------- #
# 5. retrieval isolation at scale (user A cannot see user B's records)
# --------------------------------------------------------------------------- #


async def test_retrieval_isolation_at_scale() -> None:
    """Write 1500 records for user A + 1500 for user B. Query as user A.
    Assert ZERO user B records in the results — the store enforces the
    composite isolation key, so user B's records are never in the
    candidate pool."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    config = RetrievalConfig(candidate_limit=2000, top_k=50, min_score=0.0)
    retriever = MemoryRetriever(mgr, config=config)

    base = utc_now()
    alice_records = [
        _make_record(
            record_id=f"alice-{i}",
            user_id="alice",
            context_id="ctx",
            content="alice shared topic python testing",
            memory_type=MemoryType.SEMANTIC,
            confidence=0.5,
            created_at=base + timedelta(seconds=i),
        )
        for i in range(1500)
    ]
    bob_records = [
        _make_record(
            record_id=f"bob-{i}",
            user_id="bob",
            context_id="ctx",
            content="bob secret topic python testing",
            memory_type=MemoryType.SEMANTIC,
            confidence=0.5,
            created_at=base + timedelta(seconds=i),
        )
        for i in range(1500)
    ]
    await _write_many(store, alice_records + bob_records)

    alice_results = await retriever.retrieve(
        agent_id="agent-A",
        user_id="alice",
        context_id="ctx",
        query="python testing",
    )
    alice_ids = {rm.record.record_id for rm in alice_results}

    # Zero user B records in alice's results.
    bob_ids = {f"bob-{i}" for i in range(1500)}
    assert not (alice_ids & bob_ids), (
        f"user B records leaked into user A's retrieval: {alice_ids & bob_ids}"
    )
    # All retrieved records belong to alice.
    assert alice_ids.issubset({f"alice-{i}" for i in range(1500)})


# --------------------------------------------------------------------------- #
# 6. retrieval consistency: InMemory vs SQLite return the same SET
# --------------------------------------------------------------------------- #


async def test_retrieval_inmemory_vs_sqlite_consistency() -> None:
    """Write the SAME 1500 records (with distinct created_at timestamps)
    to BOTH an InMemory store and a SQLite store. Query both with the
    same limit. Assert the SET of record_ids returned is identical.

    Pre-fix: InMemory returned records in INSERTION ORDER while SQLite
    returned them in created_at DESC order. With limit=100, the two
    stores returned DIFFERENT sets of records for the same query. Post-
    fix: both sort by created_at DESC, so the same set is returned.
    """
    base = utc_now()
    records = [
        _make_record(
            record_id=f"rec-{i}",
            content=f"python testing note number {i}",
            memory_type=MemoryType.SEMANTIC,
            confidence=0.5,
            created_at=base + timedelta(seconds=i),
        )
        for i in range(1500)
    ]

    mem_store = InMemoryMemoryStore()
    await _write_many(mem_store, records)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mem.db")
        sql_store = SQLiteMemoryStore(db_path=path)
        await _write_many(sql_store, records)

        # Query both stores with the same limit (1500 — covers all records).
        mem_recs = await mem_store.query(
            agent_id="agent-A", limit=1500
        )
        sql_recs = await sql_store.query(
            agent_id="agent-A", limit=1500
        )

        mem_ids = {r.record_id for r in mem_recs}
        sql_ids = {r.record_id for r in sql_recs}

        # Both stores return the same SET of record_ids.
        assert mem_ids == sql_ids, (
            f"InMemory and SQLite returned different record sets. "
            f"InMemory-only: {mem_ids - sql_ids}; "
            f"SQLite-only: {sql_ids - mem_ids}"
        )
        assert len(mem_ids) == 1500

        # Both stores return records in created_at DESC order (since
        # timestamps are distinct, the order is deterministic).
        mem_order = [r.record_id for r in mem_recs]
        sql_order = [r.record_id for r in sql_recs]
        assert mem_order == sql_order, (
            "InMemory and SQLite returned records in different orders. "
            f"First mismatch at index "
            f"{next((i for i,(a,b) in enumerate(zip(mem_order, sql_order, strict=False)) if a != b), 'N/A')}"
        )

        sql_store.close()


# --------------------------------------------------------------------------- #
# 7. retrieval ranks by confidence + provenance (for same content match)
# --------------------------------------------------------------------------- #


async def test_retrieval_with_varied_confidence_and_provenance() -> None:
    """Write 5 SEMANTIC memories with the SAME content (so lexical match
    is identical) but different ``confidence`` and ``source_type``. Assert
    the retriever ranks them in the expected order:

        higher confidence + better provenance → higher rank

    Expected order (highest score first):
        1. conf=0.95, source_type=tool        (prov_w=1.0)
        2. conf=0.7,  source_type=agent       (prov_w=0.7)
        3. conf=0.5,  source_type=agent       (prov_w=0.7)
        4. conf=0.3,  source_type=agent       (prov_w=0.7)
        5. conf=0.0,  source_type=unknown     (prov_w=0.3)
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    config = RetrievalConfig(top_k=10, min_score=0.0)
    retriever = MemoryRetriever(mgr, config=config)

    base = utc_now()
    specs = [
        ("low-unknown", 0.0, "unknown"),
        ("low-agent", 0.3, "agent"),
        ("mid-agent", 0.5, "agent"),
        ("high-agent", 0.7, "agent"),
        ("top-tool", 0.95, "tool"),
    ]
    for i, (rid, conf, st) in enumerate(specs):
        await store.write(
            _make_record(
                record_id=rid,
                content="python testing framework shared topic",
                memory_type=MemoryType.SEMANTIC,
                confidence=conf,
                source=st,
                source_type=st,
                created_at=base + timedelta(seconds=i),
            )
        )

    results = await retriever.retrieve(
        agent_id="agent-A", query="python testing framework"
    )
    # All 5 are retrieved (top_k=10).
    assert len(results) == 5

    order = [rm.record.record_id for rm in results]
    expected = ["top-tool", "high-agent", "mid-agent", "low-agent", "low-unknown"]
    assert order == expected, (
        f"expected ranking {expected}, got {order}. Scores: "
        f"{[(rm.record.record_id, round(rm.score, 4)) for rm in results]}"
    )

    # Scores are monotonically decreasing.
    scores = [rm.score for rm in results]
    assert scores == sorted(scores, reverse=True), (
        f"scores are not monotonically decreasing: {scores}"
    )
