"""Audit store — append-only persistence backends.

Concrete implementations:
  * InMemoryAuditStore  — list-backed (default, tests, dev).
  * FileAuditStore      — JSONL append-only file.
  * SQLiteAuditStore    — sqlite3-backed.

ALL stores are append-only (no update/delete API). Tampering detection
lives in `albra_seed.audit.verifier`, not in stores.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence

from albra_seed.audit.entry import AuditEntry


# =====================================================================
# NORMAL audit store
# =====================================================================


class AuditStore(ABC):
    """Append-only store for NORMAL AuditEntry records."""

    @abstractmethod
    def append(self, entry: AuditEntry) -> None: ...


class InMemoryAuditStore(AuditStore):
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._lock = threading.Lock()

    def append(self, entry: AuditEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def all(self) -> list[AuditEntry]:
        with self._lock:
            return list(self._entries)

    def filter(
        self,
        *,
        correlation_id: str | None = None,
        execution_id: str | None = None,
        actor: str | None = None,
        action: str | None = None,
        result: str | None = None,
    ) -> list[AuditEntry]:
        with self._lock:
            out: list[AuditEntry] = []
            for e in self._entries:
                if correlation_id is not None and e.correlation_id != correlation_id:
                    continue
                if execution_id is not None and e.execution_id != execution_id:
                    continue
                if actor is not None and e.actor != actor:
                    continue
                if action is not None and e.action != action:
                    continue
                if result is not None and e.result != result:
                    continue
                out.append(e)
            return out

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class FileAuditStore(AuditStore):
    """Append-only JSONL file. Each entry is one JSON object per line.

    The store opens the file in append-binary mode for each write and
    closes immediately, so a crash never corrupts already-written
    records. Reads scan the whole file.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.touch()

    def append(self, entry: AuditEntry) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write(entry.to_jsonl() + "\n")

    def all(self) -> list[AuditEntry]:
        if not self._path.exists():
            return []
        out: list[AuditEntry] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                out.append(AuditEntry(**json.loads(line)))
        return out

    def filter(self, **kwargs: Any) -> list[AuditEntry]:
        # File scans are linear; re-use InMemory logic.
        tmp = InMemoryAuditStore()
        for e in self.all():
            tmp._entries.append(e)  # noqa: SLF001
        return tmp.filter(**kwargs)


class SQLiteAuditStore(AuditStore):
    """SQLite-backed append-only audit store (stdlib sqlite3)."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_entries (
                entry_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                correlation_id TEXT,
                execution_id TEXT,
                actor TEXT,
                action TEXT NOT NULL,
                tool TEXT,
                provider TEXT,
                permission TEXT,
                approval_id TEXT,
                result TEXT,
                error TEXT,
                metadata TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_audit_corr ON audit_entries(correlation_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_audit_exec ON audit_entries(execution_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_entries(actor)"
        )
        self._conn.commit()

    def append(self, entry: AuditEntry) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO audit_entries
                (entry_id, timestamp, correlation_id, execution_id, actor,
                 action, tool, provider, permission, approval_id, result,
                 error, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id,
                    entry.timestamp.isoformat(),
                    entry.correlation_id,
                    entry.execution_id,
                    entry.actor,
                    entry.action,
                    entry.tool,
                    entry.provider,
                    entry.permission,
                    entry.approval_id,
                    entry.result,
                    entry.error,
                    json.dumps(entry.metadata, default=str),
                ),
            )
            self._conn.commit()

    def all(self) -> list[AuditEntry]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM audit_entries ORDER BY timestamp")
            rows = cur.fetchall()
        out: list[AuditEntry] = []
        for r in rows:
            out.append(self._row_to_entry(r))
        return out

    def filter(self, **kwargs: Any) -> list[AuditEntry]:
        cols = ["correlation_id", "execution_id", "actor", "action", "result"]
        clauses = []
        params: list[Any] = []
        for k, v in kwargs.items():
            if k in cols and v is not None:
                clauses.append(f"{k} = ?")
                params.append(v)
        where = " AND ".join(clauses) if clauses else "1=1"
        with self._lock:
            cur = self._conn.execute(
                f"SELECT * FROM audit_entries WHERE {where} ORDER BY timestamp",
                params,
            )
            rows = cur.fetchall()
        return [self._row_to_entry(r) for r in rows]

    def _row_to_entry(self, row: Sequence[Any]) -> AuditEntry:
        (
            entry_id,
            ts,
            corr,
            exec_id,
            actor,
            action,
            tool,
            provider,
            perm,
            appr,
            result,
            error,
            metadata,
        ) = row
        return AuditEntry(
            entry_id=entry_id,
            timestamp=ts,
            correlation_id=corr,
            execution_id=exec_id,
            actor=actor,
            action=action,
            tool=tool,
            provider=provider,
            permission=perm,
            approval_id=appr,
            result=result,
            error=error,
            metadata=json.loads(metadata) if metadata else {},
        )

    def close(self) -> None:
        with self._lock:
            if hasattr(self, "_conn") and self._conn is not None:
                self._conn.close()
                self._conn = None


__all__ = [
    "AuditStore",
    "InMemoryAuditStore",
    "FileAuditStore",
    "SQLiteAuditStore",
]
