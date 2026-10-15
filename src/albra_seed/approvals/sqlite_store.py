"""Durable SQLite storage for bound HITL suspended executions.

The store persists no credentials or raw tool arguments. A claim is committed
before dispatch and is never automatically reclaimed: this gives at-most-once
dispatch attempts, not exactly-once external effects. If a worker dies around
an external side effect, the outcome may be unknown and requires reconciliation.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from albra_seed.approvals.checkpoint import CheckpointValidationError, SuspensionCheckpointV1
from albra_seed.approvals.suspension import (
    SuspendedExecution,
    SuspendedExecutionError,
    SuspendedExecutionState,
    canonical_arguments_sha256,
)


_SCHEMA_VERSION = 2
@dataclass(frozen=True)
class DispatchOutcome:
    """Observation for operators; never used to automatically retry dispatch."""

    approval_id: str
    outcome: str
    observed_at: datetime
    detail: str = ""


class SQLiteSuspendedExecutionCoordinator:
    """SQLite-backed coordinator safe for independent worker instances.

    Use a local filesystem with SQLite locking semantics. Do not place this
    database on an unverified network filesystem. All transitions use
    BEGIN IMMEDIATE and conditional UPDATEs; storage failures fail closed.
    """

    def __init__(self, database_path: str | os.PathLike[str], *, timeout: float = 5.0) -> None:
        self.database_path = str(database_path)
        if self.database_path == ":memory:":
            raise ValueError("durable coordinator requires a file-backed SQLite database")
        self.timeout = timeout
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if version not in (0, 1, _SCHEMA_VERSION):
                        raise SuspendedExecutionError(
                            "unsupported HITL database schema version"
                        )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS hitl_suspended_executions (
                            approval_id TEXT PRIMARY KEY,
                            execution_id TEXT NOT NULL,
                            correlation_id TEXT NOT NULL,
                            tool_call_id TEXT NOT NULL,
                            tool_name TEXT NOT NULL,
                            arguments_sha256 TEXT NOT NULL,
                            principal_id TEXT NOT NULL,
                            deadline TEXT NOT NULL,
                            state TEXT NOT NULL CHECK (state IN (
                                'awaiting_approval', 'approved', 'rejected',
                                'expired', 'claimed'
                            )),
                            decided_by TEXT,
                            decision_reason TEXT NOT NULL DEFAULT '',
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS idx_hitl_state_deadline "
                        "ON hitl_suspended_executions(state, deadline)"
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS hitl_dispatch_outcomes (
                            approval_id TEXT PRIMARY KEY
                                REFERENCES hitl_suspended_executions(approval_id),
                            outcome TEXT NOT NULL CHECK (outcome IN (
                                'succeeded', 'failed', 'unknown'
                            )),
                            detail TEXT NOT NULL DEFAULT '',
                            observed_at TEXT NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS hitl_execution_checkpoints (
                            approval_id TEXT PRIMARY KEY
                                REFERENCES hitl_suspended_executions(approval_id),
                            schema_version INTEGER NOT NULL,
                            payload_json TEXT NOT NULL,
                            payload_sha256 TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        )
                        """
                    )
                    connection.execute("PRAGMA user_version = 2")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            # Restrict a newly-created database file. Existing deployment
            # permissions remain the operator's responsibility.
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except (sqlite3.Error, OSError) as exc:
            raise SuspendedExecutionError("HITL durable storage initialization failed") from exc

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path, timeout=self.timeout, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout = {}".format(int(self.timeout * 1000)))
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> SuspendedExecution | None:
        if row is None:
            return None
        return SuspendedExecution(
            approval_id=row["approval_id"],
            execution_id=row["execution_id"],
            correlation_id=row["correlation_id"],
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            arguments_sha256=row["arguments_sha256"],
            principal_id=row["principal_id"],
            deadline=datetime.fromisoformat(row["deadline"]),
            state=SuspendedExecutionState(row["state"]),
            decided_by=row["decided_by"],
            decision_reason=row["decision_reason"],
        )

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        value = now or datetime.now(timezone.utc)
        if value.tzinfo is None or value.utcoffset() is None:
            raise SuspendedExecutionError("now must be timezone-aware")
        return value.astimezone(timezone.utc)

    def get(self, approval_id: str) -> SuspendedExecution | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM hitl_suspended_executions WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
            return self._from_row(row)
        except sqlite3.Error as exc:
            raise SuspendedExecutionError("HITL durable storage read failed") from exc

    def register(
        self,
        *,
        approval_id: str,
        execution_id: str,
        correlation_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        principal_id: str,
        deadline: datetime,
    ) -> SuspendedExecution:
        required = (
            approval_id, execution_id, correlation_id, tool_call_id, tool_name, principal_id
        )
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise SuspendedExecutionError("missing required execution binding")
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise SuspendedExecutionError("deadline must be timezone-aware")
        now = datetime.now(timezone.utc).isoformat()
        record = SuspendedExecution(
            approval_id=approval_id,
            execution_id=execution_id,
            correlation_id=correlation_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments_sha256=canonical_arguments_sha256(arguments),
            principal_id=principal_id,
            deadline=deadline.astimezone(timezone.utc),
        )
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        """
                        INSERT INTO hitl_suspended_executions (
                            approval_id, execution_id, correlation_id, tool_call_id,
                            tool_name, arguments_sha256, principal_id, deadline,
                            state, decided_by, decision_reason, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '', ?, ?)
                        """,
                        (
                            record.approval_id, record.execution_id, record.correlation_id,
                            record.tool_call_id, record.tool_name, record.arguments_sha256,
                            record.principal_id, record.deadline.isoformat(),
                            record.state.value, now, now,
                        ),
                    )
                    connection.commit()
                except sqlite3.IntegrityError as exc:
                    connection.rollback()
                    raise SuspendedExecutionError("approval_id already registered") from exc
                except Exception:
                    connection.rollback()
                    raise
            return record
        except sqlite3.Error as exc:
            raise SuspendedExecutionError("HITL durable storage write failed") from exc

    def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        authenticated_principal_id: str | None,
        authorized: bool,
        now: datetime | None = None,
        reason: str = "",
    ) -> SuspendedExecution:
        if decision not in {"approve", "reject"}:
            raise SuspendedExecutionError("decision must be approve or reject")
        if not authorized or not authenticated_principal_id:
            raise SuspendedExecutionError("authenticated approval permission required")
        current = self._now(now)
        next_state = (
            SuspendedExecutionState.APPROVED
            if decision == "approve"
            else SuspendedExecutionState.REJECTED
        )
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    record = self._from_row(connection.execute(
                        "SELECT * FROM hitl_suspended_executions WHERE approval_id = ?",
                        (approval_id,),
                    ).fetchone())
                    if record is None:
                        raise SuspendedExecutionError("unknown approval_id")
                    if record.state is not SuspendedExecutionState.AWAITING_APPROVAL:
                        connection.commit()
                        return record
                    if current >= record.deadline:
                        next_state = SuspendedExecutionState.EXPIRED
                        decided_by = None
                        decision_reason = "deadline_passed"
                    else:
                        decided_by = authenticated_principal_id
                        decision_reason = reason if decision == "reject" else ""
                    cursor = connection.execute(
                        """
                        UPDATE hitl_suspended_executions
                        SET state = ?, decided_by = ?, decision_reason = ?, updated_at = ?
                        WHERE approval_id = ? AND state = 'awaiting_approval'
                        """,
                        (
                            next_state.value, decided_by, decision_reason,
                            current.isoformat(), approval_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise SuspendedExecutionError("atomic HITL decision lost race")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            result = self.get(approval_id)
            if result is None:
                raise SuspendedExecutionError("HITL record disappeared after decision")
            return result
        except sqlite3.Error as exc:
            raise SuspendedExecutionError("HITL durable decision failed") from exc

    def list_pending(self) -> list[SuspendedExecution]:
        """List durable pending approvals for recovery/reconciliation."""
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT * FROM hitl_suspended_executions "
                    "WHERE state = 'awaiting_approval' ORDER BY deadline"
                ).fetchall()
            return [self._from_row(row) for row in rows if row is not None]
        except sqlite3.Error as exc:
            raise SuspendedExecutionError("HITL durable storage read failed") from exc

    async def wait_for_decision(
        self,
        approval_id: str,
        *,
        timeout: float | None = None,
        poll_interval: float = 0.25,
    ) -> SuspendedExecution:
        """Observe decisions made by other workers without relying on local Events."""
        if poll_interval <= 0 or (timeout is not None and timeout < 0):
            raise ValueError("timeout must be non-negative and poll_interval positive")
        started = time.monotonic()
        while True:
            record = self.get(approval_id)
            if record is None:
                raise SuspendedExecutionError("unknown approval_id")
            now = datetime.now(timezone.utc)
            if record.state is SuspendedExecutionState.AWAITING_APPROVAL and now >= record.deadline:
                self._expire_if_due(approval_id, now)
                record = self.get(approval_id)
                if record is None:
                    raise SuspendedExecutionError("HITL record disappeared")
            if record.state is not SuspendedExecutionState.AWAITING_APPROVAL:
                return record
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError("timed out waiting for HITL decision")
            await asyncio.sleep(poll_interval)

    def _expire_if_due(self, approval_id: str, now: datetime) -> None:
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE hitl_suspended_executions
                    SET state = 'expired', decision_reason = 'deadline_passed',
                        updated_at = ?
                    WHERE approval_id = ? AND state = 'awaiting_approval' AND deadline <= ?
                    """,
                    (now.astimezone(timezone.utc).isoformat(), approval_id,
                     now.astimezone(timezone.utc).isoformat()),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise SuspendedExecutionError("HITL durable expiry failed") from exc

    def record_dispatch_outcome(
        self,
        approval_id: str,
        *,
        outcome: str,
        detail: str = "",
        observed_at: datetime | None = None,
    ) -> DispatchOutcome:
        """Record the observed outcome after a durable claim; never triggers retries."""
        if outcome not in {"succeeded", "failed", "unknown"}:
            raise SuspendedExecutionError("invalid dispatch outcome")
        timestamp = self._now(observed_at)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT state FROM hitl_suspended_executions WHERE approval_id = ?",
                        (approval_id,),
                    ).fetchone()
                    if row is None:
                        raise SuspendedExecutionError("unknown approval_id")
                    if row["state"] != SuspendedExecutionState.CLAIMED.value:
                        raise SuspendedExecutionError("dispatch outcome requires a durable claim")
                    connection.execute(
                        """
                        INSERT INTO hitl_dispatch_outcomes
                            (approval_id, outcome, detail, observed_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (approval_id, outcome, detail, timestamp.isoformat()),
                    )
                    connection.commit()
                except sqlite3.IntegrityError as exc:
                    connection.rollback()
                    existing = connection.execute(
                        "SELECT outcome, detail, observed_at FROM hitl_dispatch_outcomes "
                        "WHERE approval_id = ?", (approval_id,)
                    ).fetchone()
                    if existing and existing["outcome"] == outcome and existing["detail"] == detail:
                        return DispatchOutcome(
                            approval_id, existing["outcome"],
                            datetime.fromisoformat(existing["observed_at"]), existing["detail"]
                        )
                    raise SuspendedExecutionError("dispatch outcome already recorded") from exc
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
            return DispatchOutcome(approval_id, outcome, timestamp, detail)
        except sqlite3.Error as exc:
            raise SuspendedExecutionError("HITL dispatch outcome write failed") from exc

    def save_checkpoint(self, checkpoint: SuspensionCheckpointV1) -> None:
        """Persist a validated server-owned checkpoint at the HITL wait boundary.

        The checkpoint is not a serialized coroutine and is not sufficient by
        itself to restart Agent.run(). The caller must build it from trusted
        runtime state, after validating the exact tool call and principal.
        """
        if not isinstance(checkpoint, SuspensionCheckpointV1):
            raise SuspendedExecutionError("unsupported checkpoint object")
        payload = checkpoint.to_json()
        digest = checkpoint.payload_sha256
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT * FROM hitl_suspended_executions WHERE approval_id = ?",
                        (checkpoint.approval_id,),
                    ).fetchone()
                    if row is None:
                        raise SuspendedExecutionError("checkpoint approval does not exist")
                    bindings = {
                        "execution_id": row["execution_id"],
                        "correlation_id": row["correlation_id"],
                        "tool_call_id": row["tool_call_id"],
                        "tool_name": row["tool_name"],
                        "arguments_sha256": row["arguments_sha256"],
                        "principal_id": row["principal_id"],
                    }
                    for field, expected in bindings.items():
                        if getattr(checkpoint, field) != expected:
                            raise SuspendedExecutionError(
                                "checkpoint binding mismatch: " + field
                            )
                    if datetime.fromisoformat(row["deadline"]) != datetime.fromisoformat(checkpoint.deadline):
                        raise SuspendedExecutionError("checkpoint deadline binding mismatch")
                    if row["state"] not in ("awaiting_approval", "approved"):
                        raise SuspendedExecutionError("checkpoint state is not resumable")
                    existing = connection.execute(
                        "SELECT payload_sha256 FROM hitl_execution_checkpoints WHERE approval_id = ?",
                        (checkpoint.approval_id,),
                    ).fetchone()
                    if existing is not None:
                        if existing["payload_sha256"] != digest:
                            raise SuspendedExecutionError("checkpoint already exists with different payload")
                        connection.commit()
                        return
                    connection.execute(
                        "INSERT INTO hitl_execution_checkpoints "
                        "(approval_id, schema_version, payload_json, payload_sha256, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (checkpoint.approval_id, checkpoint.schema_version, payload, digest,
                         datetime.now(timezone.utc).isoformat()),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except sqlite3.Error as exc:
            raise SuspendedExecutionError("HITL checkpoint storage write failed") from exc

    def get_checkpoint(
        self, approval_id: str, *, runtime_compatibility: str,
        now: datetime | None = None,
    ) -> SuspensionCheckpointV1 | None:
        """Load and validate checkpoint integrity, bindings, runtime, and deadline.

        This returns data only. It deliberately does not dispatch tools or
        reconstruct an Agent.run coroutine; a trusted restore coordinator must
        repeat authorization/policy checks before any side effect.
        """
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT c.payload_json, c.payload_sha256, c.schema_version, s.* "
                    "FROM hitl_execution_checkpoints c "
                    "JOIN hitl_suspended_executions s USING (approval_id) "
                    "WHERE c.approval_id = ?",
                    (approval_id,),
                ).fetchone()
            if row is None:
                return None
            if row["schema_version"] != 1:
                raise SuspendedExecutionError("unsupported checkpoint schema version")
            raw = row["payload_json"]
            checkpoint = SuspensionCheckpointV1.from_json(raw)
            if checkpoint.payload_sha256 != row["payload_sha256"]:
                raise SuspendedExecutionError("checkpoint integrity check failed")
            if checkpoint.approval_id != approval_id:
                raise SuspendedExecutionError("checkpoint approval binding mismatch")
            for field in (
                "execution_id", "correlation_id", "tool_call_id", "tool_name",
                "arguments_sha256", "principal_id",
            ):
                if getattr(checkpoint, field) != row[field]:
                    raise SuspendedExecutionError("checkpoint binding mismatch: " + field)
            if checkpoint.runtime_compatibility != runtime_compatibility:
                raise SuspendedExecutionError("checkpoint runtime compatibility mismatch")
            state = row["state"]
            if state not in ("awaiting_approval", "approved"):
                raise SuspendedExecutionError("checkpoint execution is not resumable")
            instant = self._now(now)
            if instant >= checkpoint.deadline:
                raise SuspendedExecutionError("checkpoint deadline has expired")
            if canonical_arguments_sha256(dict(checkpoint.tool_arguments)) != checkpoint.arguments_sha256:
                raise SuspendedExecutionError("checkpoint argument digest mismatch")
            return checkpoint
        except CheckpointValidationError as exc:
            raise SuspendedExecutionError("HITL checkpoint validation failed") from exc
        except sqlite3.Error as exc:
            raise SuspendedExecutionError("HITL checkpoint storage read failed") from exc

    def recover_interrupted_dispatches(
        self, *, observed_at: datetime | None = None
    ) -> list[DispatchOutcome]:
        """Record unresolved claims as unknown after dispatch workers are fenced.

        Operators MUST stop/fence every worker that can dispatch before calling
        this method. It never resets a claim or makes it retryable; it only
        records that the external outcome could not be observed before a crash.
        Repeated calls are safe and return only outcomes created by this call.
        """
        timestamp = self._now(observed_at)
        recovered: list[DispatchOutcome] = []
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    rows = connection.execute(
                        """
                        SELECT s.approval_id
                        FROM hitl_suspended_executions AS s
                        LEFT JOIN hitl_dispatch_outcomes AS o
                          ON o.approval_id = s.approval_id
                        WHERE s.state = 'claimed' AND o.approval_id IS NULL
                        ORDER BY s.created_at, s.approval_id
                        """
                    ).fetchall()
                    for row in rows:
                        approval_id = row["approval_id"]
                        detail = "recovered_interrupted_claim; reconcile before any retry"
                        connection.execute(
                            """
                            INSERT INTO hitl_dispatch_outcomes
                                (approval_id, outcome, detail, observed_at)
                            VALUES (?, 'unknown', ?, ?)
                            """,
                            (approval_id, detail, timestamp.isoformat()),
                        )
                        recovered.append(
                            DispatchOutcome(approval_id, "unknown", timestamp, detail)
                        )
                    connection.commit()
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
            return recovered
        except sqlite3.Error as exc:
            raise SuspendedExecutionError(
                "HITL interrupted dispatch recovery failed"
            ) from exc

    def get_dispatch_outcome(self, approval_id: str) -> DispatchOutcome | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM hitl_dispatch_outcomes WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
            if row is None:
                return None
            return DispatchOutcome(
                approval_id=row["approval_id"],
                outcome=row["outcome"],
                observed_at=datetime.fromisoformat(row["observed_at"]),
                detail=row["detail"],
            )
        except sqlite3.Error as exc:
            raise SuspendedExecutionError("HITL dispatch outcome read failed") from exc

    def claim_resume(
        self,
        approval_id: str,
        *,
        execution_id: str,
        correlation_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        principal_id: str,
        now: datetime | None = None,
    ) -> SuspendedExecution:
        current = self._now(now)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    record = self._from_row(connection.execute(
                        "SELECT * FROM hitl_suspended_executions WHERE approval_id = ?",
                        (approval_id,),
                    ).fetchone())
                    if record is None:
                        raise SuspendedExecutionError("unknown approval_id")
                    if record.state is not SuspendedExecutionState.APPROVED:
                        raise SuspendedExecutionError(
                            "execution is not approved and resumable"
                        )
                    if current >= record.deadline:
                        connection.execute(
                            """
                            UPDATE hitl_suspended_executions
                            SET state = 'expired', decision_reason = 'deadline_passed',
                                updated_at = ?
                            WHERE approval_id = ? AND state = 'approved'
                            """,
                            (current.isoformat(), approval_id),
                        )
                        connection.commit()
                        raise SuspendedExecutionError("approval deadline passed")
                    matches = (
                        record.execution_id == execution_id
                        and record.correlation_id == correlation_id
                        and record.tool_call_id == tool_call_id
                        and record.tool_name == tool_name
                        and record.principal_id == principal_id
                        and record.arguments_sha256 == canonical_arguments_sha256(arguments)
                    )
                    if not matches:
                        raise SuspendedExecutionError("resumption binding mismatch")
                    cursor = connection.execute(
                        """
                        UPDATE hitl_suspended_executions
                        SET state = 'claimed', updated_at = ?
                        WHERE approval_id = ? AND state = 'approved'
                        """,
                        (current.isoformat(), approval_id),
                    )
                    if cursor.rowcount != 1:
                        raise SuspendedExecutionError("resume claim lost race")
                    connection.commit()
                except Exception:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
            result = self.get(approval_id)
            if result is None or result.state is not SuspendedExecutionState.CLAIMED:
                raise SuspendedExecutionError("HITL claim could not be verified")
            return result
        except sqlite3.Error as exc:
            raise SuspendedExecutionError("HITL durable resume claim failed") from exc


__all__ = ["DispatchOutcome", "SQLiteSuspendedExecutionCoordinator"]
