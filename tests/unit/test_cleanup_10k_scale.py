"""VERIFY-CLEANUP — WORKING cleanup >10000 records scale tests.

These tests verify that ``MemoryManager.cleanup_working()`` correctly
handles executions with MORE records than any single ``store.query()``
call can return. Both store backends cap a single ``query()`` result
(InMemory slices ``matched[:limit]``; SQLite uses ``ORDER BY ... LIMIT ?``),
so before pagination was added, ``cleanup_working`` issued ONE query with
``limit=10000`` and silently left any records beyond that bound orphaned.

Pre-fix regression (confirmed in the temp repro test
``_tmp_repro_10k.py``):
    10_500 WORKING records all with the SAME execution_id="exec-target".
    ``cleanup_working(execution_id="exec-target")`` returned 10_000 — the
    last 500 records were orphaned in the store. The assertion
    ``deleted == 10500`` FAILED.

Post-fix:
    ``cleanup_working`` paginates through the store in batches of
    ``_CLEANUP_BATCH_SIZE`` (1000). The loop terminates when the store
    returns an empty batch, when every record in a batch was already
    deleted (``seen_ids`` guard), or when the store returns fewer records
    than the batch size (exhaustion). All matching records are deleted
    regardless of the store's per-query cap.

Coverage:
    1. test_cleanup_10500_working_records_single_execution
    2. test_cleanup_15000_records_other_executions_preserved
    3. test_cleanup_pagination_no_skip
    4. test_cleanup_other_memory_types_preserved_at_scale
    5. test_cleanup_isolation_dims_preserved_at_scale
    6. test_cleanup_sqlite_10500_records

CRITICAL: every test runs with NO external credentials. All async — pytest
is configured with ``asyncio_mode = "auto"``.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from datetime import timedelta

from albra_seed.core.base import MemoryRecord
from albra_seed.core.types import MemoryType, utc_now
from albra_seed.memory import (
    InMemoryMemoryStore,
    MemoryManager,
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
    memory_type: MemoryType = MemoryType.WORKING,
    content: str = "scratch",
    confidence: float = 0.4,
    source: str = "agent",
    source_type: str = "agent",
    created_at=None,
) -> MemoryRecord:
    """Build a MemoryRecord directly — bypasses remember() provenance/
    lifecycle overhead so we can write 10_500+ records fast and with
    deterministic ``created_at`` timestamps."""
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
        created_at=ts,
        modified_at=ts,
    )


async def _write_many(store, records):
    for r in records:
        await store.write(r)


# --------------------------------------------------------------------------- #
# 1. cleanup_working handles >10000 records in a single execution
# --------------------------------------------------------------------------- #


async def test_cleanup_10500_working_records_single_execution() -> None:
    """10_500 WORKING records all with the SAME execution_id. cleanup_working
    MUST delete ALL 10_500 (not just the first 10_000 that fit in one
    ``store.query(limit=10000)`` call).

    Pre-fix: ``store.query(limit=10000)`` returned only 10_000 records.
    cleanup deleted 10_000 and left 500 orphaned. Post-fix: pagination
    iterates through the store in batches of 1000, deleting all 10_500
    regardless of the per-query cap.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    base = utc_now()
    records = [
        _make_record(
            record_id=f"mem-{i}",
            execution_id="exec-target",
            content=f"working-{i}",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(10500)
    ]
    await _write_many(store, records)

    # Sanity: 10_500 WORKING records exist for this execution. Note we
    # explicitly raise the query limit so this assertion is not itself
    # truncated by the store's per-query cap.
    recs = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-target",
        limit=20000,
    )
    assert len(recs) == 10500

    deleted = await mgr.cleanup_working(
        agent_id="agent-A", execution_id="exec-target"
    )
    assert deleted == 10500, (
        f"expected 10500 deletions, got {deleted} — the cleanup_working "
        f"pagination is broken: the store query limit truncated the "
        f"deletion set (pre-fix would return 10000 here)"
    )

    # Post-condition: zero WORKING records remain for this execution.
    remaining = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-target",
        limit=20000,
    )
    assert remaining == []


# --------------------------------------------------------------------------- #
# 2. cleanup_working preserves OTHER executions at scale (>10_000 each)
# --------------------------------------------------------------------------- #


async def test_cleanup_15000_records_other_executions_preserved() -> None:
    """Write 10_500 WORKING for exec-A + 5_000 WORKING for exec-B (same
    isolation bucket). Cleanup exec-A. Assert:

        * exec-A records are ALL gone (all 10_500 — pagination needed).
        * exec-B records are ALL preserved (all 5_000).

    Pre-fix: exec-A cleanup would delete only 10_000 of its 10_500 records,
    leaving 500 orphaned in the store AND corrupting subsequent cleanup
    runs because the orphans would be re-queried forever.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    base = utc_now()
    a_records = [
        _make_record(
            record_id=f"a-{i}",
            execution_id="exec-A",
            content=f"a-{i}",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(10500)
    ]
    b_records = [
        _make_record(
            record_id=f"b-{i}",
            execution_id="exec-B",
            content=f"b-{i}",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(5000)
    ]
    await _write_many(store, a_records + b_records)

    deleted = await mgr.cleanup_working(
        agent_id="agent-A", execution_id="exec-A"
    )
    assert deleted == 10500, (
        f"expected 10500 exec-A deletions, got {deleted} — pagination "
        f"truncated the cleanup set"
    )

    # exec-A: zero records remain.
    a_left = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-A",
        limit=20000,
    )
    assert a_left == [], (
        f"exec-A should be empty, but {len(a_left)} records survived"
    )

    # exec-B: all 5_000 records preserved — cleanup_working(execution_id="exec-A")
    # must NOT touch records with a different execution_id, even at scale.
    b_left = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-B",
        limit=20000,
    )
    assert len(b_left) == 5000, (
        f"exec-B should be untouched (5000 records), got {len(b_left)} — "
        f"cleanup_working(execution_id='exec-A') leaked across executions"
    )
    # Identity check — no b-* record was deleted.
    surviving_ids = {r.record_id for r in b_left}
    assert surviving_ids == {f"b-{i}" for i in range(5000)}


# --------------------------------------------------------------------------- #
# 3. cleanup_working deletes EXACTLY the matching set — no skips, no extras
# --------------------------------------------------------------------------- #


async def test_cleanup_pagination_no_skip() -> None:
    """Write 10_500 WORKING records (single execution). cleanup_working
    MUST delete EXACTLY 10_500 — not 10_000 (truncation), not 11_000 (double
    counting via re-queries).

    Verifies the count via the InMemory store's tombstone dict, which is
    the ground-truth record of every successful ``delete()`` call. If the
    pagination loop re-queries after each delete and the store keeps
    returning the same records, the seen_ids guard prevents double-counting
    AND prevents an infinite loop. If the loop forgets a batch, the
    tombstone count is below 10_500.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    base = utc_now()
    records = [
        _make_record(
            record_id=f"mem-{i}",
            execution_id="exec-exact",
            content=f"working-{i}",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(10500)
    ]
    await _write_many(store, records)

    deleted = await mgr.cleanup_working(
        agent_id="agent-A", execution_id="exec-exact"
    )
    assert deleted == 10500, (
        f"cleanup returned {deleted}, expected EXACTLY 10500"
    )

    # Ground truth: count tombstones the store actually wrote. Each
    # successful store.delete() appends to ``_tombstones`` — so this count
    # cannot be inflated by re-queries (the InMemory store physically
    # removes deleted rows, so a re-query won't return them).
    tombstones = store._tombstones  # noqa: SLF001 — intentional white-box
    assert len(tombstones) == 10500, (
        f"tombstone count {len(tombstones)} != 10500 — cleanup deleted "
        f"{deleted} but only wrote {len(tombstones)} tombstones "
        f"(double-deletes would show as deleted > tombstones, skips as "
        f"deleted < tombstones)"
    )

    # No record left in the live table.
    remaining = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-exact",
        limit=20000,
    )
    assert remaining == []

    # Lifecycle events: exactly one DELETED event per record (no duplicates
    # if seen_ids guard works). We filter to DELETED-state events whose
    # reason is "working_cleanup".
    deleted_events = [
        rid
        for rid, events in mgr.lifecycle._events.items()  # noqa: SLF001
        for ev in events
        if ev.state.value == "deleted" and ev.reason == "working_cleanup"
    ]
    assert len(deleted_events) == 10500, (
        f"lifecycle recorded {len(deleted_events)} DELETED events, "
        f"expected 10500 — pagination either skipped records or "
        f"double-counted via re-queries"
    )

    # Sanity: every record_id is unique in the lifecycle events —
    # double-delete would not inflate the count (the lifecycle appends
    # to a per-record list, but each record appears at most once across
    # ``_events.keys()``).
    unique_record_ids = {
        rid
        for rid, events in mgr.lifecycle._events.items()  # noqa: SLF001
        for ev in events
        if ev.state.value == "deleted" and ev.reason == "working_cleanup"
    }
    assert len(unique_record_ids) == 10500


# --------------------------------------------------------------------------- #
# 4. cleanup_working preserves other memory TYPES at scale (>10_000 WORKING)
# --------------------------------------------------------------------------- #


async def test_cleanup_other_memory_types_preserved_at_scale() -> None:
    """Write 10_500 WORKING + 500 EPISODIC + 500 FACTUAL with the SAME
    execution_id. Cleanup WORKING. Assert:

        * WORKING == 0 (all 10_500 deleted — pagination needed).
        * EPISODIC == 500 (untouched).
        * FACTUAL == 500 (untouched).

    Pre-fix: WORKING cleanup would have left 500 WORKING records orphaned
    AND would have potentially returned the wrong count. EPISODIC + FACTUAL
    were never affected because cleanup_working filters
    ``memory_type=WORKING`` at the store level — this test confirms that
    contract survives the pagination refactor.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    base = utc_now()
    work_records = [
        _make_record(
            record_id=f"work-{i}",
            execution_id="exec-multi",
            memory_type=MemoryType.WORKING,
            content=f"work-{i}",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(10500)
    ]
    epi_records = [
        _make_record(
            record_id=f"epi-{i}",
            execution_id="exec-multi",
            memory_type=MemoryType.EPISODIC,
            content=f"epi-{i}",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(500)
    ]
    # FACTUAL — confidence >= 0.9 + source_type in {tool, external,
    # verified_provider} would be required by remember(); since we bypass
    # remember() and write directly, the FACTUAL validation isn't enforced
    # here. The cleanup_working path does NOT re-validate is_fact — it
    # only filters by memory_type.
    fact_records = [
        _make_record(
            record_id=f"fact-{i}",
            execution_id="exec-multi",
            memory_type=MemoryType.FACTUAL,
            content=f"fact-{i}",
            confidence=0.95,
            source="tool",
            source_type="tool",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(500)
    ]
    await _write_many(store, work_records + epi_records + fact_records)

    deleted = await mgr.cleanup_working(
        agent_id="agent-A", execution_id="exec-multi"
    )
    assert deleted == 10500, (
        f"expected 10500 WORKING deletions, got {deleted} — pagination "
        f"truncated the cleanup set"
    )

    # WORKING: zero (all 10_500 deleted via pagination).
    work_left = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-multi",
        limit=20000,
    )
    assert work_left == []

    # EPISODIC: all 500 preserved.
    epi_left = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.EPISODIC,
        execution_id="exec-multi",
        limit=10000,
    )
    assert len(epi_left) == 500, (
        f"EPISODIC count {len(epi_left)} != 500 — cleanup leaked across "
        f"memory types"
    )

    # FACTUAL: all 500 preserved.
    fact_left = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.FACTUAL,
        execution_id="exec-multi",
        limit=10000,
    )
    assert len(fact_left) == 500, (
        f"FACTUAL count {len(fact_left)} != 500 — cleanup leaked across "
        f"memory types"
    )


# --------------------------------------------------------------------------- #
# 5. cleanup_working preserves isolation dims (user_id) at scale
# --------------------------------------------------------------------------- #


async def test_cleanup_isolation_dims_preserved_at_scale() -> None:
    """Write 10_500 WORKING for user A + 10_500 WORKING for user B with the
    SAME execution_id (same agent + domain). Cleanup user A. Assert:

        * user A == 0 (all 10_500 deleted — pagination needed).
        * user B == 10_500 (untouched — different isolation bucket).

    Pre-fix: user A cleanup would have deleted only 10_000 of its 10_500
    records. Worse — if the store had returned user A's records interleaved
    with user B's (e.g. a query without a user_id filter), the wrong
    records could have been deleted. This test confirms the store-level
    isolation key filter (agent_id + domain + user_id + context_id) is
    applied IN ADDITION to the execution_id filter, and that pagination
    does not widen the scope.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    base = utc_now()
    a_records = [
        _make_record(
            record_id=f"alice-{i}",
            user_id="alice",
            execution_id="exec-shared",
            content=f"alice-{i}",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(10500)
    ]
    b_records = [
        _make_record(
            record_id=f"bob-{i}",
            user_id="bob",
            execution_id="exec-shared",
            content=f"bob-{i}",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(10500)
    ]
    await _write_many(store, a_records + b_records)

    deleted = await mgr.cleanup_working(
        agent_id="agent-A", user_id="alice", execution_id="exec-shared"
    )
    assert deleted == 10500, (
        f"expected 10500 alice deletions, got {deleted} — pagination "
        f"truncated the cleanup set"
    )

    # Alice's bucket: empty.
    alice_left = await store.query(
        agent_id="agent-A",
        user_id="alice",
        memory_type=MemoryType.WORKING,
        execution_id="exec-shared",
        limit=20000,
    )
    assert alice_left == [], (
        f"alice bucket should be empty, {len(alice_left)} records survived"
    )

    # Bob's bucket: all 10_500 preserved — even though it shares
    # execution_id with alice's records.
    bob_left = await store.query(
        agent_id="agent-A",
        user_id="bob",
        memory_type=MemoryType.WORKING,
        execution_id="exec-shared",
        limit=20000,
    )
    assert len(bob_left) == 10500, (
        f"bob bucket should be untouched (10500 records), got "
        f"{len(bob_left)} — cleanup leaked across isolation buckets at scale"
    )
    surviving_ids = {r.record_id for r in bob_left}
    assert surviving_ids == {f"bob-{i}" for i in range(10500)}


# --------------------------------------------------------------------------- #
# 6. SQLite: cleanup_working handles >10_000 records in one execution
# --------------------------------------------------------------------------- #


async def test_cleanup_sqlite_10500_records() -> None:
    """Same as test 1, but on SQLiteMemoryStore — verifies:

        * The SQL ``WHERE execution_id = ?`` clause + ``ORDER BY created_at
          DESC LIMIT ?`` paginates correctly: 11 batches of 1000 + a final
          batch of 500 = 10_500 records all deleted.
        * 10_500 tombstones are written to the ``memory_tombstones``
          table — proving the SQLite delete path does not choke on scale
          and that the audit trail is complete.
        * The live ``memory_records`` table has 0 rows for this execution
          after cleanup (verified via a direct SQL COUNT, not just via
          ``store.query()`` — so we are not relying on the same code path
          we are testing).
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mem.db")
        store = SQLiteMemoryStore(db_path=path)
        mgr = MemoryManager(store=store)

        base = utc_now()
        records = [
            _make_record(
                record_id=f"sql-mem-{i}",
                execution_id="exec-target",
                content=f"working-{i}",
                created_at=base + timedelta(seconds=i),
            )
            for i in range(10500)
        ]
        await _write_many(store, records)

        # Wrap in asyncio.wait_for to catch any infinite loop in the
        # pagination refactor (e.g. if seen_ids guard fails and the store
        # keeps returning the same records). 120 seconds is more than
        # enough for 10_500 SQLite writes + tombstones on a tmpfs-backed
        # tempfile.
        deleted = await asyncio.wait_for(
            mgr.cleanup_working(
                agent_id="agent-A", execution_id="exec-target"
            ),
            timeout=120.0,
        )
        assert deleted == 10500, (
            f"expected 10500 SQLite deletions, got {deleted} — pagination "
            f"truncated the cleanup set on SQLiteMemoryStore"
        )

        # store.query() returns 0 — confirms the high-level path sees no
        # surviving records.
        remaining = await store.query(
            agent_id="agent-A",
            memory_type=MemoryType.WORKING,
            execution_id="exec-target",
            limit=20000,
        )
        assert remaining == []

        # Direct SQL COUNT — ground truth, independent of the query path
        # under test. This catches any tombstone-respect bug in
        # ``store.query()`` that would mask surviving records.
        with sqlite3.connect(path) as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM memory_records "
                "WHERE agent_id = ? AND memory_type = ? AND execution_id = ?",
                ("agent-A", MemoryType.WORKING.value, "exec-target"),
            )
            (live_count,) = cur.fetchone()
        assert live_count == 0, (
            f"live table count {live_count} != 0 — cleanup_working left "
            f"records in memory_records"
        )

        # Tombstone count — proves the delete path actually wrote 10_500
        # tombstones (audit trail complete, no skipped deletes).
        with sqlite3.connect(path) as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM memory_tombstones WHERE reason = ?",
                ("working_cleanup",),
            )
            (tombstone_count,) = cur.fetchone()
        assert tombstone_count == 10500, (
            f"tombstone count {tombstone_count} != 10500 — cleanup "
            f"deleted {deleted} records but only wrote {tombstone_count} "
            f"tombstones (audit trail incomplete)"
        )

        store.close()
