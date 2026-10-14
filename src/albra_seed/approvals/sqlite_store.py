"""Durable, cross-process approval and external-dispatch coordination.

SQLite is the source of truth. State changes use BEGIN IMMEDIATE and compare-
and-set predicates, so independent workers cannot both win a transition.
A dispatch interrupted after its durable claim is marked uncertain on recovery
and is NEVER automatically re-queued: an external provider may already have
performed the effect. Exactly-once external effects require provider-side
idempotency using the returned call_id.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from albra_seed.approvals.request import ApprovalRequest
from albra_seed.core.types import ApprovalStatus, utc_now


_ALLOWED = {
    ApprovalStatus.PENDING.value: {
        ApprovalStatus.APPROVED.value,
        ApprovalStatus.REJECTED.value,
        ApprovalStatus.EXPIRED.value,
        ApprovalStatus.ESCALATED.value,
    }
}


class SQLiteApprovalStore:
    """Durable approval records and a fail-closed dispatch ledger.

    Use a file-backed database shared by all workers (not separate local
    files). SQLite WAL and busy_timeout support concurrent readers/writers.
    """

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self.db_path = str(db_path)
        if self.db_path == ":memory:":
            raise ValueError("SQLiteApprovalStore requires a file-backed database shared by workers")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    call_id TEXT,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_status
                    ON approvals(status);
                CREATE INDEX IF NOT EXISTS idx_approvals_call
                    ON approvals(call_id);
                CREATE TABLE IF NOT EXISTS approval_dispatch (
                    call_id TEXT PRIMARY KEY,
                    approval_id TEXT NOT NULL REFERENCES approvals(approval_id),
                    state TEXT NOT NULL CHECK(state IN
                        ('ready','claimed','completed','uncertain','failed')),
                    claim_token TEXT,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_dispatch_state
                    ON approval_dispatch(state);
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path, timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        return conn

    @staticmethod
    def _dump(request: ApprovalRequest) -> str:
        return request.model_dump_json()

    def create(self, request: ApprovalRequest, *, call_id: str | None = None) -> ApprovalRequest:
        """Insert an approval exactly once, optionally linked to its source call."""
        now = utc_now().isoformat()
        payload = request.model_dump(mode="json")
        if call_id:
            payload.setdefault("context_ref", {})["call_id"] = call_id
        saved = ApprovalRequest.model_validate(payload)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO approvals(approval_id,call_id,status,request_json,updated_at) "
                    "VALUES(?,?,?,?,?)",
                    (saved.approval_id, call_id, saved.status.value, self._dump(saved), now),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return saved

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT request_json FROM approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
        return ApprovalRequest.model_validate_json(row["request_json"]) if row else None

    def list(self, *, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        with self._connect() as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT request_json FROM approvals ORDER BY rowid"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT request_json FROM approvals WHERE status=? ORDER BY rowid",
                    (status.value,),
                ).fetchall()
        return [ApprovalRequest.model_validate_json(r["request_json"]) for r in rows]

    def transition(
        self, approval_id: str, target: ApprovalStatus, *,
        decided_by: str | None = None, reason: str = "",
    ) -> bool:
        """Atomically transition PENDING to one allowed decision; one worker wins."""
        if target.value not in _ALLOWED[ApprovalStatus.PENDING.value]:
            raise ValueError(f"invalid approval transition target: {target.value}")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT request_json FROM approvals WHERE approval_id=? AND status=?",
                    (approval_id, ApprovalStatus.PENDING.value),
                ).fetchone()
                if row is None:
                    conn.rollback()
                    return False
                request = ApprovalRequest.model_validate_json(row["request_json"])
                if target == ApprovalStatus.APPROVED:
                    request.mark_approved(by=decided_by or "unknown")
                elif target == ApprovalStatus.REJECTED:
                    request.mark_rejected(by=decided_by or "unknown", reason=reason)
                elif target == ApprovalStatus.EXPIRED:
                    request.mark_expired()
                else:
                    request.mark_escalated()
                conn.execute(
                    "UPDATE approvals SET status=?,request_json=?,updated_at=? "
                    "WHERE approval_id=? AND status=?",
                    (target.value, self._dump(request), utc_now().isoformat(),
                     approval_id, ApprovalStatus.PENDING.value),
                )
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def prepare_dispatch(self, approval_id: str, call_id: str) -> bool:
        """Create a single dispatch slot, only for an approved linked call."""
        if not call_id:
            raise ValueError("call_id must be non-empty")
        now = utc_now().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT status,call_id,request_json FROM approvals WHERE approval_id=?",
                    (approval_id,),
                ).fetchone()
                if row is None or row["status"] != ApprovalStatus.APPROVED.value:
                    conn.rollback()
                    return False
                stored_call = row["call_id"]
                context_call = json.loads(row["request_json"]).get("context_ref", {}).get("call_id")
                if stored_call and stored_call != call_id:
                    conn.rollback()
                    raise ValueError("approval is bound to a different call_id")
                if context_call and context_call != call_id:
                    conn.rollback()
                    raise ValueError("approval context is bound to a different call_id")
                conn.execute(
                    "INSERT INTO approval_dispatch(call_id,approval_id,state,created_at,updated_at) "
                    "VALUES(?,?,'ready',?,?)",
                    (call_id, approval_id, now, now),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                conn.rollback()
                return False
            except Exception:
                conn.rollback()
                raise

    def claim_dispatch(self, call_id: str, claim_token: str) -> bool:
        """Atomic single-worker claim. A claim is never silently made retryable."""
        if not claim_token:
            raise ValueError("claim_token must be non-empty")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE approval_dispatch SET state='claimed',claim_token=?,updated_at=? "
                "WHERE call_id=? AND state='ready'",
                (claim_token, utc_now().isoformat(), call_id),
            )
            conn.commit()
            return cur.rowcount == 1

    def complete_dispatch(self, call_id: str, claim_token: str, result: Any) -> bool:
        """Persist successful result; stale or competing claimants cannot complete."""
        encoded = json.dumps(result, sort_keys=True, default=str)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE approval_dispatch SET state='completed',result_json=?,updated_at=? "
                "WHERE call_id=? AND state='claimed' AND claim_token=?",
                (encoded, utc_now().isoformat(), call_id, claim_token),
            )
            conn.commit()
            return cur.rowcount == 1

    def fail_dispatch(self, call_id: str, claim_token: str, error: str) -> bool:
        """Mark a claimed dispatch uncertain; it is deliberately not retryable."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE approval_dispatch SET state='uncertain',error=?,updated_at=? "
                "WHERE call_id=? AND state='claimed' AND claim_token=?",
                (error, utc_now().isoformat(), call_id, claim_token),
            )
            conn.commit()
            return cur.rowcount == 1

    def recover_interrupted_dispatches(self) -> int:
        """Fail closed after restart: claimed means outcome may be externally unknown."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE approval_dispatch SET state='uncertain',"
                "error=COALESCE(error,'worker interrupted; external outcome unknown'),"
                "updated_at=? WHERE state='claimed'",
                (utc_now().isoformat(),),
            )
            conn.commit()
            return cur.rowcount

    def dispatch(self, call_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM approval_dispatch WHERE call_id=?", (call_id,)
            ).fetchone()
        return dict(row) if row else None
