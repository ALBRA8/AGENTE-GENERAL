"""BLOQ 5 — Memory forget() / audit tests.

These tests verify that `MemoryManager.forget`:
    - returns True when the record existed, False otherwise
    - writes a tombstone to the underlying store carrying `by` and `reason`
    - emits a structured audit event to a configured `audit_sink`
      (action="memory.forget", record_id, by, reason)
    - is silent (no error, no audit) when no sink is configured — CORE_ONLY
    - persists the tombstone across SQLite reopens

The tombstone is the audit trail for SQLite stores. The optional sink allows
in-memory stores to forward events to an external auditor (e.g. the audit
subsystem, a SIEM, a structured logger).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile

import pytest

from albra_seed.core.types import MemoryType
from albra_seed.memory import (
    InMemoryMemoryStore,
    MemoryManager,
    SQLiteMemoryStore,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _run(coro):
    return asyncio.run(coro)


async def _seed_record(mgr: MemoryManager, *, agent_id: str = "agent-A") -> str:
    rec = await mgr.remember(
        agent_id=agent_id,
        content="ephemeral memory to be forgotten",
        memory_type=MemoryType.EPISODIC,
    )
    return rec.record_id


# --------------------------------------------------------------------------- #
# Forget return value
# --------------------------------------------------------------------------- #


def test_forget_returns_true_when_record_exists():
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    rid = _run(_seed_record(mgr))
    ok = _run(mgr.forget(rid, reason="test", by="alice"))
    assert ok is True
    # record is gone
    assert _run(mgr.get(rid)) is None


def test_forget_returns_false_when_record_missing():
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    ok = _run(mgr.forget("does-not-exist", reason="noop", by="alice"))
    assert ok is False


# --------------------------------------------------------------------------- #
# Tombstone propagation: by + reason
# --------------------------------------------------------------------------- #


def test_forget_creates_tombstone_with_by_and_reason():
    """InMemoryMemoryStore MUST record `by` and `reason` in the tombstone."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    rid = _run(_seed_record(mgr))

    ok = _run(mgr.forget(rid, reason="gdpr", by="alice"))
    assert ok is True

    tomb = store._tombstones.get(rid)  # type: ignore[attr-defined]
    assert tomb is not None
    assert tomb["by"] == "alice"
    assert tomb["reason"] == "gdpr"
    assert "deleted_at" in tomb
    assert tomb["deleted_at"] != "now"  # must be a real ISO timestamp


def test_forget_tombstone_defaults_to_none_when_unspecified():
    """If `by`/`reason` are not supplied, the tombstone records `None`."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    rid = _run(_seed_record(mgr))

    ok = _run(mgr.forget(rid))
    assert ok is True
    tomb = store._tombstones.get(rid)  # type: ignore[attr-defined]
    assert tomb is not None
    assert tomb["by"] is None
    assert tomb["reason"] is None


# --------------------------------------------------------------------------- #
# Audit sink
# --------------------------------------------------------------------------- #


def test_forget_emits_audit_event_when_sink_configured():
    """When an `audit_sink` is configured, `forget` MUST call it with a
    structured event dict: action / record_id / by / reason."""
    received: list[dict] = []

    def sink(event: dict) -> None:
        received.append(event)

    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store, audit_sink=sink)
    rid = _run(_seed_record(mgr))

    ok = _run(mgr.forget(rid, reason="gdpr", by="alice"))
    assert ok is True
    assert len(received) == 1
    evt = received[0]
    assert evt["action"] == "memory.forget"
    assert evt["record_id"] == rid
    assert evt["by"] == "alice"
    assert evt["reason"] == "gdpr"


def test_forget_emits_audit_event_when_sink_is_async():
    """An async audit_sink must be awaited."""
    received: list[dict] = []

    async def sink(event: dict) -> None:
        received.append(event)

    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store, audit_sink=sink)
    rid = _run(_seed_record(mgr))

    ok = _run(mgr.forget(rid, reason="policy", by="bob"))
    assert ok is True
    assert len(received) == 1
    assert received[0]["by"] == "bob"
    assert received[0]["reason"] == "policy"


def test_forget_no_audit_when_sink_none():
    """Default MemoryManager (no sink) MUST NOT raise and MUST NOT emit
    anything — CORE_ONLY mode is unchanged."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)  # no audit_sink
    rid = _run(_seed_record(mgr))

    # Must not raise
    ok = _run(mgr.forget(rid, reason="test", by="alice"))
    assert ok is True


def test_forget_no_audit_when_record_missing():
    """If the record does not exist, no audit event is emitted (forget
    returns False without touching the sink)."""
    received: list[dict] = []
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store, audit_sink=lambda e: received.append(e))

    ok = _run(mgr.forget("nope", reason="noop", by="alice"))
    assert ok is False
    assert received == []


# --------------------------------------------------------------------------- #
# SQLite persistence
# --------------------------------------------------------------------------- #


def test_forget_sqlite_persists_tombstone():
    """SQLite store MUST persist the tombstone (with by + reason) across
    reopens."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mem.db")
        store = SQLiteMemoryStore(db_path=path)
        mgr = MemoryManager(store=store)
        rec = _run(
            mgr.remember(
                agent_id="agent-A",
                content="to be deleted",
                memory_type=MemoryType.EPISODIC,
            )
        )
        rid = rec.record_id
        ok = _run(mgr.forget(rid, reason="gdpr-erasure", by="compliance"))
        assert ok is True
        store.close()

        # Reopen — tombstone must still be there
        store2 = SQLiteMemoryStore(db_path=path)
        fetched = _run(store2.read(rid))
        assert fetched is None  # record is gone
        # Tombstone table must carry by + reason
        conn = sqlite3.connect(path)
        try:
            cur = conn.execute(
                "SELECT by, reason FROM memory_tombstones WHERE record_id = ?",
                (rid,),
            )
            row = cur.fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "compliance"
        assert row[1] == "gdpr-erasure"
        store2.close()


# --------------------------------------------------------------------------- #
# Docstring honesty
# --------------------------------------------------------------------------- #


def test_docstring_matches_implementation():
    """The `forget()` docstring MUST accurately describe the implementation:
        - writes a tombstone
        - tombstone contains record_id, timestamp, by, reason
        - optional audit_sink is invoked with action="memory.forget"
        - returns True on delete, False on missing
    """
    import inspect

    doc = MemoryManager.forget.__doc__ or ""
    assert "tombstone" in doc.lower()
    assert "by" in doc
    assert "reason" in doc
    assert "audit" in doc.lower() or "audit_sink" in doc
    assert "memory.forget" in doc
    assert "True" in doc and "False" in doc

    # Verify the implementation actually does what the docstring claims.
    sig = inspect.signature(MemoryManager.forget)
    assert "reason" in sig.parameters
    assert "by" in sig.parameters
    assert sig.parameters["reason"].default is None
    assert sig.parameters["by"].default is None
