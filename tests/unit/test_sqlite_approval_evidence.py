"""Durability and concurrency tests for SQLite approval evidence."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from albra_seed.approvals import (
    ApprovalEngine,
    SQLiteApprovalEvidenceStore,
    SQLiteApprovalStore,
    ApprovalEvidence,
)
from albra_seed.core.types import ApprovalStatus


def test_sqlite_engine_evidence_survives_restart(tmp_path):
    db_path = tmp_path / "approvals.sqlite3"
    engine = ApprovalEngine(store=SQLiteApprovalStore(db_path))
    pending = asyncio.run(engine.request("external.send"))
    result = __import__("asyncio").run(engine.approve(pending.approval_id, "reviewer"))
    assert result.status == ApprovalStatus.APPROVED
    assert engine.verify_evidence_chain()

    restarted = ApprovalEngine(store=SQLiteApprovalStore(db_path))
    assert restarted.verify_evidence_chain()
    records = restarted.evidence.list_for(pending.approval_id)
    assert len(records) == 1
    assert records[0].status == ApprovalStatus.APPROVED
    assert records[0].decided_by == "reviewer"


def test_concurrent_sqlite_evidence_appends_preserve_one_chain(tmp_path):
    db_path = tmp_path / "approvals.sqlite3"
    stores = [SQLiteApprovalEvidenceStore(db_path) for _ in range(8)]

    def append(index):
        record = ApprovalEvidence(
            approval_id=f"approval-{index}",
            status=ApprovalStatus.APPROVED,
            decided_by=f"reviewer-{index}",
        )
        return stores[index].append(record)

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(append, range(8)))

    reopened = SQLiteApprovalEvidenceStore(db_path)
    assert len(reopened.all_records()) == 8
    assert reopened.verify_chain()
    assert len({record.record_hash for record in records}) == 8
