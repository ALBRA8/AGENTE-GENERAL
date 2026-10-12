"""P1-A — cleanup_working scale tests.

Verifies that ``MemoryManager.cleanup_working()`` correctly handles
>1000 records, both when a single execution has >1000 WORKING records AND
when the target execution's records are the OLDEST (sorted last by
``created_at DESC``) in a store with 1500+ WORKING records across
executions.

Pre-fix regression:
  - ``cleanup_working`` called ``store.query(limit=1000)`` then post-filtered
    by ``execution_id`` in Python. If a single execution had >1000 records,
    OR if the target execution's records were the OLDEST 500 (positions
    1001-1500 in ``created_at DESC`` order), they were never returned by
    the query and therefore never deleted.

Post-fix:
  - ``cleanup_working`` passes ``execution_id`` to ``store.query()`` at the
    SQL / in-memory scan level, with ``limit=10000``. The store filters by
    ``execution_id`` BEFORE applying the limit, so all matching records are
    returned regardless of how they're ordered in the store.

Coverage:
    1. test_cleanup_1500_working_records_same_execution
    2. test_cleanup_when_target_records_are_oldest
    3. test_cleanup_does_not_delete_other_executions
    4. test_cleanup_does_not_delete_episodic_or_factual
    5. test_cleanup_preserves_isolation_dims
    6. test_cleanup_pagination_no_infinite_loop
    7. test_cleanup_1500_working_records_same_execution_sqlite
    8. test_cleanup_when_target_records_are_oldest_sqlite

CRITICAL: every test runs with NO external credentials. All async — pytest
is configured with ``asyncio_mode = "auto"``.
"""

from __future__ import annotations

import asyncio
import os
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
    lifecycle overhead so we can write 1500+ records fast and with
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
# 1. cleanup_working handles >1000 records in a single execution
# --------------------------------------------------------------------------- #


async def test_cleanup_1500_working_records_same_execution() -> None:
    """1500 WORKING records all with the SAME execution_id. cleanup_working
    MUST delete ALL 1500 (not just the first 1000).

    Pre-fix: ``store.query(limit=1000)`` returned only the 1000 newest;
    cleanup deleted 1000 and left 500 orphaned. Post-fix: the
    execution_id filter is applied at the store level, so the query
    returns ALL matching records (up to limit=10000) regardless of
    created_at ordering.
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
        for i in range(1500)
    ]
    await _write_many(store, records)

    # Sanity: 1500 WORKING records exist for this execution.
    recs = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-target",
        limit=10000,
    )
    assert len(recs) == 1500

    deleted = await mgr.cleanup_working(
        agent_id="agent-A", execution_id="exec-target"
    )
    assert deleted == 1500, (
        f"expected 1500 deletions, got {deleted} — the cleanup_working "
        f"limit=10000 was not honoured or the execution_id filter is broken"
    )

    # Post-condition: zero WORKING records remain for this execution.
    remaining = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-target",
        limit=10000,
    )
    assert remaining == []


# --------------------------------------------------------------------------- #
# 2. cleanup_working finds TARGET records even when they are the OLDEST
# --------------------------------------------------------------------------- #


async def test_cleanup_when_target_records_are_oldest() -> None:
    """Write 1500 WORKING records: 1000 with execution_id="OTHER" (NEWEST)
    and 500 with execution_id="TARGET" (OLDEST). created_at DESC ordering
    puts the 1000 OTHER records first and the 500 TARGET records last
    (positions 1001-1500).

    Pre-fix: ``store.query(limit=1000)`` returned only the 1000 OTHER
    records. The Python post-filter ``[r for r in records if r.execution_id
    == "TARGET"]`` yielded an EMPTY list, so 0 TARGET records were deleted.
    Post-fix: the execution_id filter is applied at the store level, so
    the query returns ALL 500 TARGET records (regardless of their
    created_at position) and all 500 are deleted.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    base = utc_now()

    # 1000 OTHER records — NEWEST (created_at = base + 0..999 seconds).
    other_records = [
        _make_record(
            record_id=f"other-{i}",
            execution_id="OTHER",
            content=f"other-{i}",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(1000)
    ]
    # 500 TARGET records — OLDEST (created_at = base - 1..500 seconds).
    target_records = [
        _make_record(
            record_id=f"target-{i}",
            execution_id="TARGET",
            content=f"target-{i}",
            created_at=base - timedelta(seconds=i + 1),
        )
        for i in range(500)
    ]
    await _write_many(store, other_records + target_records)

    # Sanity: a plain query (no execution_id filter) with limit=1000 returns
    # only OTHER records — TARGET records are at positions 1001-1500.
    plain = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        limit=1000,
    )
    assert len(plain) == 1000
    assert all(r.execution_id == "OTHER" for r in plain)
    # None of the TARGET records are in the plain top-1000 query.
    assert not any(r.execution_id == "TARGET" for r in plain)

    # cleanup_working(execution_id="TARGET") MUST delete all 500 TARGET
    # records even though they are the OLDEST 500 in the store.
    deleted = await mgr.cleanup_working(
        agent_id="agent-A", execution_id="TARGET"
    )
    assert deleted == 500, (
        f"expected 500 TARGET deletions, got {deleted} — the store-level "
        f"execution_id filter is not being applied (pre-fix bug would "
        f"return 0 here because the TARGET records are beyond limit=1000 "
        f"in created_at DESC order)"
    )

    # All 1000 OTHER records MUST survive.
    survivors = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="OTHER",
        limit=10000,
    )
    assert len(survivors) == 1000

    # No TARGET records remain.
    target_left = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="TARGET",
        limit=10000,
    )
    assert target_left == []


# --------------------------------------------------------------------------- #
# 3. cleanup_working does NOT delete records from other executions
# --------------------------------------------------------------------------- #


async def test_cleanup_does_not_delete_other_executions() -> None:
    """Write WORKING records for 3 different execution_ids. Cleanup one;
    the other two are untouched.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    base = utc_now()
    for exec_id, count in (("exec-A", 5), ("exec-B", 7), ("exec-C", 3)):
        for i in range(count):
            await store.write(
                _make_record(
                    record_id=f"{exec_id}-{i}",
                    execution_id=exec_id,
                    content=f"{exec_id}-{i}",
                    created_at=base + timedelta(seconds=i),
                )
            )

    deleted = await mgr.cleanup_working(
        agent_id="agent-A", execution_id="exec-B"
    )
    assert deleted == 7

    # exec-A and exec-C are untouched.
    a = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-A",
        limit=100,
    )
    c = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-C",
        limit=100,
    )
    b = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-B",
        limit=100,
    )
    assert len(a) == 5
    assert len(c) == 3
    assert len(b) == 0


# --------------------------------------------------------------------------- #
# 4. cleanup_working does NOT delete EPISODIC or FACTUAL records
# --------------------------------------------------------------------------- #


async def test_cleanup_does_not_delete_episodic_or_factual() -> None:
    """WORKING + EPISODIC + FACTUAL records, all with the same
    execution_id. cleanup_working touches ONLY the WORKING ones."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    base = utc_now()
    # 10 WORKING + 10 EPISODIC + 10 FACTUAL, same execution_id.
    for i in range(10):
        await store.write(
            _make_record(
                record_id=f"work-{i}",
                execution_id="exec-X",
                memory_type=MemoryType.WORKING,
                content=f"work-{i}",
                created_at=base + timedelta(seconds=i),
            )
        )
        await store.write(
            _make_record(
                record_id=f"epi-{i}",
                execution_id="exec-X",
                memory_type=MemoryType.EPISODIC,
                content=f"epi-{i}",
                created_at=base + timedelta(seconds=i),
            )
        )
        # FACTUAL records are NOT subject to working cleanup — they survive
        # regardless of execution_id matching.
        await store.write(
            _make_record(
                record_id=f"fact-{i}",
                execution_id="exec-X",
                memory_type=MemoryType.FACTUAL,
                content=f"fact-{i}",
                confidence=0.95,
                source="tool",
                source_type="tool",
                created_at=base + timedelta(seconds=i),
            )
        )

    deleted = await mgr.cleanup_working(
        agent_id="agent-A", execution_id="exec-X"
    )
    assert deleted == 10

    # All EPISODIC + FACTUAL records survive.
    epi = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.EPISODIC,
        execution_id="exec-X",
        limit=100,
    )
    fact = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.FACTUAL,
        execution_id="exec-X",
        limit=100,
    )
    work = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-X",
        limit=100,
    )
    assert len(epi) == 10
    assert len(fact) == 10
    assert work == []


# --------------------------------------------------------------------------- #
# 5. cleanup_working preserves isolation dims (user_id / context_id)
# --------------------------------------------------------------------------- #


async def test_cleanup_preserves_isolation_dims() -> None:
    """WORKING records for user A and user B with the SAME execution_id.
    cleanup_working(user_id=A) deletes ONLY user A's records; user B's
    records are preserved — even though they share the execution_id.

    This verifies that the store-level execution_id filter is applied
    IN ADDITION TO (not instead of) the composite isolation key filter.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    base = utc_now()
    # 5 records for alice + 5 for bob, SAME execution_id.
    for i in range(5):
        await store.write(
            _make_record(
                record_id=f"alice-{i}",
                user_id="alice",
                execution_id="exec-shared",
                content=f"alice-{i}",
                created_at=base + timedelta(seconds=i),
            )
        )
        await store.write(
            _make_record(
                record_id=f"bob-{i}",
                user_id="bob",
                execution_id="exec-shared",
                content=f"bob-{i}",
                created_at=base + timedelta(seconds=i),
            )
        )

    # Cleanup alice's bucket — scoped by (agent, domain, user_id, exec_id).
    deleted = await mgr.cleanup_working(
        agent_id="agent-A", user_id="alice", execution_id="exec-shared"
    )
    assert deleted == 5

    # Alice's bucket is empty.
    alice_left = await store.query(
        agent_id="agent-A",
        user_id="alice",
        memory_type=MemoryType.WORKING,
        execution_id="exec-shared",
        limit=100,
    )
    assert alice_left == []

    # Bob's bucket is untouched — even though it shares execution_id.
    bob_left = await store.query(
        agent_id="agent-A",
        user_id="bob",
        memory_type=MemoryType.WORKING,
        execution_id="exec-shared",
        limit=100,
    )
    assert len(bob_left) == 5
    assert {r.record_id for r in bob_left} == {f"bob-{i}" for i in range(5)}


# --------------------------------------------------------------------------- #
# 6. cleanup_working completes in finite time on 2500 records (no infinite
#    loop, no pagination stall)
# --------------------------------------------------------------------------- #


async def test_cleanup_pagination_no_infinite_loop() -> None:
    """Write 2500 WORKING records (under the limit=10000, but enough to
    stress the cleanup loop). Verify cleanup completes in finite time and
    deletes ALL 2500.

    The cleanup loop iterates the records returned by store.query() and
    deletes them one by one. If the loop has a bug (e.g. re-querying after
    each delete and never converging), it would hang. The asyncio.wait_for
    guard catches that.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    base = utc_now()
    records = [
        _make_record(
            record_id=f"mem-{i}",
            execution_id="exec-bulk",
            content=f"working-{i}",
            created_at=base + timedelta(seconds=i),
        )
        for i in range(2500)
    ]
    await _write_many(store, records)

    # 30 seconds is more than enough for 2500 in-memory deletes; if cleanup
    # hangs (e.g. re-querying after each delete), the timeout fires.
    deleted = await asyncio.wait_for(
        mgr.cleanup_working(agent_id="agent-A", execution_id="exec-bulk"),
        timeout=30.0,
    )
    assert deleted == 2500

    # No records remain.
    remaining = await store.query(
        agent_id="agent-A",
        memory_type=MemoryType.WORKING,
        execution_id="exec-bulk",
        limit=10000,
    )
    assert remaining == []


# --------------------------------------------------------------------------- #
# 7. SQLite: cleanup_working handles 1500 records in one execution
# --------------------------------------------------------------------------- #


async def test_cleanup_1500_working_records_same_execution_sqlite() -> None:
    """Same as test 1, but on SQLiteMemoryStore — verifies the SQL
    ``execution_id = ?`` filter handles scale and the tombstone write
    path doesn't choke on 1500 deletes."""
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
            for i in range(1500)
        ]
        await _write_many(store, records)

        deleted = await mgr.cleanup_working(
            agent_id="agent-A", execution_id="exec-target"
        )
        assert deleted == 1500

        # No WORKING records remain in the live table for this execution.
        remaining = await store.query(
            agent_id="agent-A",
            memory_type=MemoryType.WORKING,
            execution_id="exec-target",
            limit=10000,
        )
        assert remaining == []
        store.close()


# --------------------------------------------------------------------------- #
# 8. SQLite: cleanup_working finds TARGET records even when OLDEST
# --------------------------------------------------------------------------- #


async def test_cleanup_when_target_records_are_oldest_sqlite() -> None:
    """Same as test 2, but on SQLiteMemoryStore — verifies the SQL
    ``WHERE execution_id = ?`` clause filters BEFORE the ``LIMIT``, so
    TARGET records at positions 1001-1500 in ``created_at DESC`` order
    are still returned and deleted.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mem.db")
        store = SQLiteMemoryStore(db_path=path)
        mgr = MemoryManager(store=store)

        base = utc_now()
        other_records = [
            _make_record(
                record_id=f"sql-other-{i}",
                execution_id="OTHER",
                content=f"other-{i}",
                created_at=base + timedelta(seconds=i),
            )
            for i in range(1000)
        ]
        target_records = [
            _make_record(
                record_id=f"sql-target-{i}",
                execution_id="TARGET",
                content=f"target-{i}",
                created_at=base - timedelta(seconds=i + 1),
            )
            for i in range(500)
        ]
        await _write_many(store, other_records + target_records)

        # Pre-fix sanity: a plain query (no execution_id filter) with
        # limit=1000 returns only OTHER records.
        plain = await store.query(
            agent_id="agent-A",
            memory_type=MemoryType.WORKING,
            limit=1000,
        )
        assert len(plain) == 1000
        assert all(r.execution_id == "OTHER" for r in plain)

        deleted = await mgr.cleanup_working(
            agent_id="agent-A", execution_id="TARGET"
        )
        assert deleted == 500

        # All OTHER records survive.
        survivors = await store.query(
            agent_id="agent-A",
            memory_type=MemoryType.WORKING,
            execution_id="OTHER",
            limit=10000,
        )
        assert len(survivors) == 1000
        store.close()
