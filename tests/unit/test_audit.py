"""Unit tests for the audit subpackage (F2-G).

Covers BOTH the NORMAL audit log and the SEPARATE tamper-evident
audit log.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from albra_seed.audit import (
    AuditEntry,
    AuditLog,
    ChainVerifier,
    FileAuditStore,
    InMemoryAuditStore,
    SQLiteAuditStore,
    TamperEvidentAuditLog,
    TamperEvidentEntry,
)


# =====================================================================
# NORMAL audit log
# =====================================================================


def test_normal_audit_log_appends():
    """AuditLog.record appends; all() returns the records in order."""
    log = AuditLog()
    e1 = log.record(action="tool.invoke", actor="alice", result="success")
    e2 = log.record(action="tool.invoke", actor="bob", result="failure", error="boom")
    all_entries = log.all()
    assert len(all_entries) == 2
    assert all_entries[0].entry_id == e1.entry_id
    assert all_entries[1].action == "tool.invoke"
    assert all_entries[1].result == "failure"
    assert all_entries[1].error == "boom"
    assert all_entries[0].actor == "alice"


def test_audit_log_filter_by_correlation_id():
    """AuditLog.filter_by_correlation_id narrows to that id."""
    log = AuditLog()
    cid_a = "corr_AAA"
    cid_b = "corr_BBB"
    log.record(action="a1", actor="alice", correlation_id=cid_a)
    log.record(action="a2", actor="alice", correlation_id=cid_a)
    log.record(action="b1", actor="bob", correlation_id=cid_b)
    log.record(action="a3", actor="alice", correlation_id=cid_a)
    matches = log.filter_by_correlation_id(cid_a)
    assert len(matches) == 3
    assert all(m.correlation_id == cid_a for m in matches)
    b_matches = log.filter_by_correlation_id(cid_b)
    assert len(b_matches) == 1
    assert b_matches[0].action == "b1"

    # Also test filter_by_actor + filter_by_execution_id.
    assert len(log.filter_by_actor("alice")) == 3
    assert len(log.filter_by_actor("bob")) == 1


def test_audit_log_file_store_appends():
    """FileAuditStore persists appends across instances (append-only)."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "audit.jsonl")
        store1 = FileAuditStore(path)
        log1 = AuditLog(store=store1)
        log1.record(action="x", actor="alice")
        log1.record(action="y", actor="bob")

        store2 = FileAuditStore(path)
        log2 = AuditLog(store=store2)
        log2.record(action="z", actor="carol")

        all_entries = log2.all()
        assert len(all_entries) == 3
        assert [e.action for e in all_entries] == ["x", "y", "z"]


def test_audit_log_sqlite_store_filters():
    """SQLiteAuditStore supports filtering by correlation_id."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "audit.sqlite")
        store = SQLiteAuditStore(path)
        log = AuditLog(store=store)
        log.record(action="a1", correlation_id="c1")
        log.record(action="a2", correlation_id="c2")
        log.record(action="a3", correlation_id="c1")
        assert len(log.filter_by_correlation_id("c1")) == 2
        assert len(log.filter_by_correlation_id("c2")) == 1


# =====================================================================
# TAMPER-EVIDENT audit log — SEPARATE from normal
# =====================================================================


def test_tamper_evident_chain_valid():
    """A freshly-appended chain verifies as intact."""
    te = TamperEvidentAuditLog()
    h1 = te.append({"action": "tool.invoke", "actor": "alice"})
    h2 = te.append({"action": "tool.invoke", "actor": "bob"})
    h3 = te.append({"action": "approval.grant", "actor": "carol"})
    assert h1 != h2 != h3
    assert te.count() == 3

    result = te.verify_chain()
    assert result.valid is True
    assert result.broken_at is None
    assert result.chain_length == 3


def test_tamper_evident_detects_modification():
    """Modifying a record's content invalidates its record_hash; the
    chain detects it and reports the broken index."""
    te = TamperEvidentAuditLog()
    te.append({"action": "tool.invoke", "actor": "alice"})
    te.append({"action": "tool.invoke", "actor": "bob"})
    te.append({"action": "approval.grant", "actor": "carol"})

    # Tamper with the SECOND record's action field — its record_hash
    # no longer matches the computed hash.
    entries = te.all()
    entries[1].action = "TAMPERED_ACTION"

    result = te.verify_chain()
    assert result.valid is False
    assert result.broken_at == 1
    assert result.kind == "record_hash_mismatch"

    evidences = te.detect_tampering()
    assert len(evidences) >= 1
    assert evidences[0].index == 1


def test_tamper_evident_detects_prev_hash_tamper():
    """Modifying a record's prev_hash breaks the chain at that index."""
    te = TamperEvidentAuditLog()
    te.append({"action": "x"})
    te.append({"action": "y"})
    te.append({"action": "z"})
    entries = te.all()
    # Mutate the third entry's prev_hash so it doesn't match the second's record_hash.
    entries[2].prev_hash = "0" * 64
    result = te.verify_chain()
    assert result.valid is False
    assert result.broken_at == 2
    assert result.kind == "prev_hash_mismatch"


def test_tamper_evident_separate_from_normal():
    """NORMAL AuditLog and TAMPER_EVIDENT TamperEvidentAuditLog are
    SEPARATE classes; entries of one kind are not stored in the other."""
    normal = AuditLog()
    te = TamperEvidentAuditLog()

    normal.record(action="normal.event", actor="alice")
    te.append({"action": "te.event", "actor": "alice"})

    # Normal log has AuditEntry instances; TE log has TamperEvidentEntry.
    normal_entries = normal.all()
    te_entries = te.all()
    assert all(isinstance(e, AuditEntry) for e in normal_entries)
    assert all(isinstance(e, TamperEvidentEntry) for e in te_entries)
    # Cross-pollination: TE entries have prev_hash/record_hash; normal don't.
    assert hasattr(te_entries[0], "prev_hash")
    assert hasattr(te_entries[0], "record_hash")
    assert not hasattr(normal_entries[0], "record_hash")
    # Counts are independent.
    assert normal.count() == 1
    assert te.count() == 1


def test_chain_verifier_returns_broken_at():
    """ChainVerifier.verify returns broken_at index for first broken link."""
    verifier = ChainVerifier()
    te = TamperEvidentAuditLog()
    te.append({"action": "a"})
    te.append({"action": "b"})
    te.append({"action": "c"})
    te.append({"action": "d"})

    # Tamper with index 2 (third entry).
    entries = te.all()
    entries[2].action = "TAMPERED"

    result = verifier.verify(entries)
    assert result.valid is False
    assert result.broken_at == 2
    assert result.kind == "record_hash_mismatch"
    assert result.expected_hash is not None
    assert result.actual_hash is not None
    assert result.expected_hash != result.actual_hash


def test_chain_verifier_empty_chain_is_valid():
    """An empty chain verifies as valid (vacuously)."""
    verifier = ChainVerifier()
    result = verifier.verify([])
    assert result.valid is True
    assert result.chain_length == 0


def test_audit_entry_to_jsonl_roundtrip():
    """AuditEntry.to_jsonl produces parseable JSON; TamperEvidentEntry too."""
    e = AuditEntry(action="x", actor="alice", metadata={"k": "v"})
    line = e.to_jsonl()
    parsed = AuditEntry.model_validate_json(line)
    assert parsed.action == "x"
    assert parsed.actor == "alice"
    assert parsed.metadata == {"k": "v"}

    te = TamperEvidentEntry(action="y", actor="bob")
    te.prev_hash = "abc"
    te.record_hash = te.compute_record_hash("abc")
    line2 = te.to_jsonl()
    parsed2 = TamperEvidentEntry.model_validate_json(line2)
    assert parsed2.action == "y"
    assert parsed2.prev_hash == "abc"
    assert parsed2.record_hash == te.record_hash
