"""T9 — organization-scoped memory isolation (S4.4).

Every memory operation (remember / recall / get / forget / count /
cleanup) carries an ``org_id`` tenant dimension enforced by
``MemoryIsolator``. These tests prove:

1. The org bucket is part of the composite key — two organizations with
   identical agent/domain/user/context dims never see each other's
   records (both InMemory and SQLite stores).
2. Identity validation: an agent_id carrying the D-014 ``org:<X>`` prefix
   REJECTS an ``org_id`` from another organization (PermissionError).
3. Cross-org reads/deletes by record id are refused (PermissionError).
4. Malformed org ids are rejected (ValueError) before touching a store.
5. Legacy compatibility: records written WITHOUT org (pre-S4.4 shape) are
   still readable, never mix with org-scoped buckets, and a legacy SQLite
   database migrates additively (ALTER TABLE) without data loss.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from albra_seed.core.base import MemoryRecord
from albra_seed.core.types import MemoryType
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.memory.isolator import MemoryIsolator
from albra_seed.memory.manager import MemoryManager
from albra_seed.memory.sqlite_store import SQLiteMemoryStore

ORG_A = "org_aaaa1111"
ORG_B = "org_bbbb2222"
# D-014 tenant identity: org:<organizationId>#<agenteLogico>
AGENT_A = f"org:{ORG_A}#ventas"
AGENT_B = f"org:{ORG_B}#ventas"


def _run_any(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------
# Isolator
# ---------------------------------------------------------------------


def test_isolator_org_is_first_key_component():
    k_org = MemoryIsolator.key_for("agent-x", "crm", "u1", "c1", ORG_A)
    k_legacy = MemoryIsolator.key_for("agent-x", "crm", "u1", "c1")
    k_other = MemoryIsolator.key_for("agent-x", "crm", "u1", "c1", ORG_B)
    assert k_org != k_legacy  # legacy "" bucket never mixes with org buckets
    assert k_org != k_other  # two orgs never share a bucket
    assert str(k_org) == f"mem::org={ORG_A}::agent-x::crm::u1::c1"
    # Legacy string format is preserved byte-for-byte (index compatibility).
    assert str(k_legacy) == "mem::agent-x::crm::u1::c1"


def test_isolator_rejects_malformed_org_id():
    with pytest.raises(ValueError):
        MemoryIsolator.validate_org_id("not-an-org")
    with pytest.raises(ValueError):
        MemoryIsolator.validate_org_id("org_")
    with pytest.raises(ValueError):
        MemoryIsolator.key_for("agent-x", org_id="customer 42")


def test_isolator_binds_org_to_d014_agent_identity():
    # Consistent pairing is accepted.
    MemoryIsolator.assert_org_consistent(AGENT_A, ORG_A)
    # Cross-tenant pairing is a hard violation.
    with pytest.raises(PermissionError):
        MemoryIsolator.assert_org_consistent(AGENT_A, ORG_B)
    # Agents without the org prefix impose no org constraint (legacy).
    MemoryIsolator.assert_org_consistent("albra-agent", ORG_A)
    assert MemoryIsolator.org_of_agent(AGENT_A) == ORG_A
    assert MemoryIsolator.org_of_agent("albra-agent") is None


def test_isolator_key_for_validates_org_agent_pair():
    with pytest.raises(PermissionError):
        MemoryIsolator.key_for(AGENT_A, "crm", "u", "c", ORG_B)


# ---------------------------------------------------------------------
# Cross-organization negative tests (both stores)
# ---------------------------------------------------------------------


@pytest.mark.parametrize("store_factory", [InMemoryMemoryStore, SQLiteMemoryStore])
async def test_cross_org_recall_returns_nothing(store_factory):
    store = store_factory()
    mgr = MemoryManager(store=store)
    await mgr.remember(
        agent_id=AGENT_A,
        org_id=ORG_A,
        domain="crm",
        user_id="ct_1",
        context_id="cv_1",
        memory_type=MemoryType.EPISODIC,
        content={"secret": "org-A-turn"},
        confidence=0.3,
    )
    # Org B queries the SAME agent/domain/user/context dims — nothing leaks.
    cross = await mgr.recall(
        agent_id=AGENT_A.replace(ORG_A, ORG_B),
        org_id=ORG_B,
        domain="crm",
        user_id="ct_1",
        context_id="cv_1",
    )
    assert cross == []
    # The legacy bucket (no org) also cannot see org-scoped records.
    legacy = await mgr.recall(
        agent_id=AGENT_A, domain="crm", user_id="ct_1", context_id="cv_1"
    )
    assert legacy == []
    # The owning org still sees its record.
    own = await mgr.recall(
        agent_id=AGENT_A, org_id=ORG_A, domain="crm", user_id="ct_1", context_id="cv_1"
    )
    assert len(own) == 1
    assert own[0].content == {"secret": "org-A-turn"}


@pytest.mark.parametrize("store_factory", [InMemoryMemoryStore, SQLiteMemoryStore])
async def test_cross_org_get_and_forget_refused(store_factory):
    store = store_factory()
    mgr = MemoryManager(store=store)
    record = await mgr.remember(
        agent_id=AGENT_A,
        org_id=ORG_A,
        domain="crm",
        memory_type=MemoryType.EPISODIC,
        content="solo-org-A",
        confidence=0.3,
    )
    with pytest.raises(PermissionError):
        await mgr.get(record.record_id, org_id=ORG_B)
    with pytest.raises(PermissionError):
        await mgr.forget(record.record_id, org_id=ORG_B)
    # Record survives the denied cross-org delete; owner can still delete.
    assert await mgr.get(record.record_id, org_id=ORG_A) is not None
    assert await mgr.forget(record.record_id, org_id=ORG_A) is True


@pytest.mark.parametrize("store_factory", [InMemoryMemoryStore, SQLiteMemoryStore])
async def test_count_is_org_scoped(store_factory):
    store = store_factory()
    mgr = MemoryManager(store=store)
    for org, agent in ((ORG_A, AGENT_A), (ORG_B, AGENT_B)):
        for i in range(3):
            await mgr.remember(
                agent_id=agent,
                org_id=org,
                domain="crm",
                memory_type=MemoryType.EPISODIC,
                content=f"turn-{org}-{i}",
                confidence=0.3,
            )
    assert await mgr.count(agent_id=AGENT_A, domain="crm", org_id=ORG_A) == 3
    assert await mgr.count(agent_id=AGENT_B, domain="crm", org_id=ORG_B) == 3
    # A count pairing agent/org from different tenants is a violation.
    with pytest.raises(PermissionError):
        await mgr.count(agent_id=AGENT_A, domain="crm", org_id=ORG_B)


async def test_manager_rejects_org_agent_mismatch_on_write():
    mgr = MemoryManager(store=InMemoryMemoryStore())
    with pytest.raises(PermissionError):
        await mgr.remember(
            agent_id=AGENT_A,
            org_id=ORG_B,
            domain="crm",
            memory_type=MemoryType.EPISODIC,
            content="x",
        )
    with pytest.raises(ValueError):
        await mgr.remember(
            agent_id=AGENT_A,
            org_id="bad-org",
            domain="crm",
            memory_type=MemoryType.EPISODIC,
            content="x",
        )


# ---------------------------------------------------------------------
# Legacy compatibility (no data loss, additive migration)
# ---------------------------------------------------------------------


def test_legacy_sqlite_database_migrates_without_data_loss(tmp_path: Path):
    db_path = tmp_path / "legacy-memory.sqlite3"
    # Create a PRE-S4.4 database manually (schema without org_id).
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE memory_records (
            record_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            domain TEXT NOT NULL DEFAULT 'default',
            user_id TEXT,
            context_id TEXT,
            memory_type TEXT NOT NULL,
            content_json TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            is_fact INTEGER NOT NULL DEFAULT 0,
            provenance_id TEXT,
            execution_id TEXT,
            correlation_id TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            modified_at TEXT NOT NULL,
            full_json TEXT NOT NULL
        );
        CREATE TABLE memory_tombstones (
            record_id TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL,
            by TEXT,
            reason TEXT
        );
        """
    )
    legacy_full = (
        '{"record_id":"mem-legacy-1","agent_id":"agent-old","domain":"default",'
        '"user_id":null,"context_id":null,"memory_type":"episodic",'
        '"content":"pre-s44","confidence":0.3,"source":"agent",'
        '"source_type":"agent","evidence_ids":[],"provenance_id":null,'
        '"execution_id":null,"correlation_id":null,"version":1,'
        '"created_at":"2026-01-01T00:00:00","modified_at":"2026-01-01T00:00:00",'
        '"is_fact":false}'
    )
    conn.execute(
        "INSERT INTO memory_records (record_id, agent_id, domain, memory_type,"
        " content_json, confidence, is_fact, version, created_at, modified_at,"
        " full_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "mem-legacy-1",
            "agent-old",
            "default",
            "episodic",
            '"pre-s44"',
            0.3,
            0,
            1,
            "2026-01-01T00:00:00",
            "2026-01-01T00:00:00",
            legacy_full,
        ),
    )
    conn.commit()
    conn.close()

    # Opening with the S4.4 store migrates additively and keeps the record.
    store = SQLiteMemoryStore(db_path)
    got = _run_any(store.read("mem-legacy-1"))
    assert got is not None
    assert got.content == "pre-s44"
    assert got.org_id is None  # legacy bucket

    # The legacy record stays in the legacy bucket and is NOT visible to
    # any organization query; new org-scoped writes coexist safely.
    mgr = MemoryManager(store=store)
    legacy_view = _run_any(
        mgr.recall(agent_id="agent-old", domain="default")
    )
    assert len(legacy_view) == 1
    org_view = _run_any(
        mgr.recall(agent_id="agent-old", domain="default", org_id=ORG_A)
    )
    assert org_view == []
    new_record = _run_any(
        mgr.remember(
            agent_id=f"org:{ORG_A}#ventas",
            org_id=ORG_A,
            domain="crm",
            memory_type=MemoryType.EPISODIC,
            content="post-s44",
            confidence=0.3,
        )
    )
    assert new_record.org_id == ORG_A
    store.close()


def _run_any(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def test_same_agent_id_different_orgs_isolated_sqlite(tmp_path: Path):
    """The pathological case — SAME org-less agent string used under two
    organizations — cannot cross: the org bucket separates them. (Agent ids
    WITH an org prefix can't even attempt this: the identity guard rejects
    the mismatch before any store call.)"""
    store = SQLiteMemoryStore(tmp_path / "shared.sqlite3")
    mgr = MemoryManager(store=store)
    shared_agent = "albra-agent"  # no D-014 org prefix
    await mgr.remember(
        agent_id=shared_agent,
        org_id=ORG_A,
        domain="crm",
        memory_type=MemoryType.EPISODIC,
        content="A-secret",
        confidence=0.3,
    )
    await mgr.remember(
        agent_id=shared_agent,
        org_id=ORG_B,
        domain="crm",
        memory_type=MemoryType.EPISODIC,
        content="B-secret",
        confidence=0.3,
    )
    only_a = await mgr.recall(
        agent_id=shared_agent, org_id=ORG_A, domain="crm"
    )
    only_b = await mgr.recall(
        agent_id=shared_agent, org_id=ORG_B, domain="crm"
    )
    assert [r.content for r in only_a] == ["A-secret"]
    assert [r.content for r in only_b] == ["B-secret"]


async def test_cleanup_working_is_org_scoped():
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    record_a = await mgr.remember(
        agent_id=AGENT_A,
        org_id=ORG_A,
        domain="crm",
        user_id="ct_1",
        context_id="cv_1",
        memory_type=MemoryType.WORKING,
        content="working-A",
        execution_id="exec-A",
    )
    record_b = await mgr.remember(
        agent_id=AGENT_B,
        org_id=ORG_B,
        domain="crm",
        user_id="ct_1",
        context_id="cv_1",
        memory_type=MemoryType.WORKING,
        content="working-B",
        execution_id="exec-B",
    )
    deleted = await mgr.cleanup_working(
        agent_id=AGENT_A, org_id=ORG_A, domain="crm", user_id="ct_1",
        context_id="cv_1", execution_id="exec-A",
    )
    assert deleted == 1
    # Org B's working memory is untouched.
    assert await mgr.get(record_b.record_id, org_id=ORG_B) is not None
    assert await mgr.get(record_a.record_id, org_id=ORG_A) is None


def test_memory_record_accepts_org_id():
    record = MemoryRecord(
        agent_id=AGENT_A,
        org_id=ORG_A,
        memory_type=MemoryType.EPISODIC,
        content="x",
    )
    assert record.org_id == ORG_A
    default = MemoryRecord(
        agent_id=AGENT_A, memory_type=MemoryType.EPISODIC, content="x"
    )
    assert default.org_id is None
