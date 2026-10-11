"""Learning store — abstract + InMemory + SQLite.

Same pattern as `feedback/store.py`, `evidence/store.py` and
`provenance/store.py`:
  * `LearningStore`             — ABC.
  * `InMemoryLearningStore`     — dict-backed (default, tests, dev).
  * `SQLiteLearningStore`        — sqlite3-backed (stdlib only).

Methods (mirrors the other SEED stores):
  * `write(proposal)            -> proposal_id`     (upsert on proposal_id)
  * `read(proposal_id)          -> LearningProposal | None`
  * `query(*, agent_id, status, target_component, limit) -> list[LearningProposal]`
  * `update(proposal_id, **fields) -> bool`            (partial update)
  * `count()                    -> int`

`update` is a partial-update primitive used by `LearningManager` to
transition statuses. It does NOT change `proposal_id` or `created_at`.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from albra_seed.learning.types import LearningProposal, ProposalStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_proposals (
    proposal_id      TEXT PRIMARY KEY,
    agent_id         TEXT NOT NULL,
    signal_type      TEXT NOT NULL,
    target_component TEXT NOT NULL,
    target_id        TEXT,
    status           TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 0.0,
    execution_id     TEXT,
    correlation_id   TEXT,
    observation      TEXT NOT NULL,
    proposed_change  TEXT NOT NULL,
    rationale        TEXT NOT NULL,
    reviewed_by      TEXT,
    reviewed_at      TEXT,
    applied_at       TEXT,
    rejection_reason TEXT,
    source_feedback_ids   TEXT NOT NULL DEFAULT '[]',
    source_decision_ids   TEXT NOT NULL DEFAULT '[]',
    source_execution_ids  TEXT NOT NULL DEFAULT '[]',
    evidence_ids          TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    metadata_json    TEXT NOT NULL DEFAULT '{}',
    full_json        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lp_agent    ON learning_proposals(agent_id);
CREATE INDEX IF NOT EXISTS idx_lp_status   ON learning_proposals(status);
CREATE INDEX IF NOT EXISTS idx_lp_target  ON learning_proposals(target_component);
CREATE INDEX IF NOT EXISTS idx_lp_corr     ON learning_proposals(correlation_id);
"""


class LearningStore(ABC):
    """Abstract persistence backend for LearningProposal."""

    @abstractmethod
    async def write(self, proposal: LearningProposal) -> str:
        raise NotImplementedError

    @abstractmethod
    async def read(self, proposal_id: str) -> LearningProposal | None:
        raise NotImplementedError

    @abstractmethod
    async def query(
        self,
        *,
        agent_id: str | None = None,
        status: ProposalStatus | None = None,
        target_component: str | None = None,
        limit: int = 50,
    ) -> list[LearningProposal]:
        raise NotImplementedError

    @abstractmethod
    async def update(self, proposal_id: str, **fields: Any) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def count(self) -> int:
        raise NotImplementedError


class InMemoryLearningStore(LearningStore):
    """Dict-backed store — thread-safe via a single lock."""

    def __init__(self) -> None:
        self._records: dict[str, LearningProposal] = {}
        self._lock = threading.Lock()

    async def write(self, proposal: LearningProposal) -> str:
        with self._lock:
            self._records[proposal.proposal_id] = proposal
        return proposal.proposal_id

    async def read(self, proposal_id: str) -> LearningProposal | None:
        with self._lock:
            return self._records.get(proposal_id)

    async def query(
        self,
        *,
        agent_id: str | None = None,
        status: ProposalStatus | None = None,
        target_component: str | None = None,
        limit: int = 50,
    ) -> list[LearningProposal]:
        with self._lock:
            out: list[LearningProposal] = []
            for rec in self._records.values():
                if agent_id is not None and rec.agent_id != agent_id:
                    continue
                if status is not None and rec.status != status:
                    continue
                if target_component is not None and rec.target_component != target_component:
                    continue
                out.append(rec)
            out.sort(key=lambda r: r.created_at, reverse=True)
            return out[: max(0, int(limit))]

    async def update(self, proposal_id: str, **fields: Any) -> bool:
        with self._lock:
            rec = self._records.get(proposal_id)
            if rec is None:
                return False
            for k, v in fields.items():
                if k in ("proposal_id", "created_at"):
                    # immutable — ignored
                    continue
                if hasattr(rec, k):
                    setattr(rec, k, v)
            return True

    async def count(self) -> int:
        with self._lock:
            return len(self._records)


class SQLiteLearningStore(LearningStore):
    """sqlite3-backed store (stdlib only).

    Same row pattern as the other SQLite stores in the SEED: store the
    canonical JSON of the full pydantic model in `full_json`, plus
    indexed columns for the fields most commonly filtered on.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        if self._db_path == ":memory:":
            self._conn: sqlite3.Connection = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
        else:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    async def write(self, proposal: LearningProposal) -> str:
        data = proposal.model_dump(mode="json")
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO learning_proposals
                (proposal_id, agent_id, signal_type, target_component,
                 target_id, status, confidence, execution_id, correlation_id,
                 observation, proposed_change, rationale, reviewed_by,
                 reviewed_at, applied_at, rejection_reason,
                 source_feedback_ids, source_decision_ids, source_execution_ids,
                 evidence_ids, created_at, metadata_json, full_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    proposal.proposal_id,
                    proposal.agent_id,
                    proposal.signal_type.value,
                    proposal.target_component,
                    proposal.target_id,
                    proposal.status.value,
                    float(proposal.confidence),
                    proposal.execution_id,
                    proposal.correlation_id,
                    proposal.observation,
                    proposal.proposed_change,
                    proposal.rationale,
                    proposal.reviewed_by,
                    str(proposal.reviewed_at) if proposal.reviewed_at else None,
                    str(proposal.applied_at) if proposal.applied_at else None,
                    proposal.rejection_reason,
                    json.dumps(list(proposal.source_feedback_ids or [])),
                    json.dumps(list(proposal.source_decision_ids or [])),
                    json.dumps(list(proposal.source_execution_ids or [])),
                    json.dumps(list(proposal.evidence_ids or [])),
                    str(proposal.created_at),
                    json.dumps(proposal.metadata, default=str),
                    json.dumps(data),
                ),
            )
            self._conn.commit()
        return proposal.proposal_id

    def _row(self, row: sqlite3.Row | tuple) -> LearningProposal:
        full = row["full_json"] if isinstance(row, sqlite3.Row) else row[-1]
        return LearningProposal.model_validate_json(full)

    async def read(self, proposal_id: str) -> LearningProposal | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM learning_proposals WHERE proposal_id = ?",
                (proposal_id,),
            )
            row = cur.fetchone()
        return self._row(row) if row else None

    async def query(
        self,
        *,
        agent_id: str | None = None,
        status: ProposalStatus | None = None,
        target_component: str | None = None,
        limit: int = 50,
    ) -> list[LearningProposal]:
        clauses: list[str] = []
        params: list[object] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if target_component is not None:
            clauses.append("target_component = ?")
            params.append(target_component)
        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(max(0, int(limit)))
        with self._lock:
            cur = self._conn.execute(
                f"SELECT * FROM learning_proposals WHERE {where} "
                "ORDER BY created_at DESC LIMIT ?",
                params,
            )
            rows = cur.fetchall()
        return [self._row(r) for r in rows]

    async def update(self, proposal_id: str, **fields: Any) -> bool:
        if not fields:
            rec = await self.read(proposal_id)
            return rec is not None
        # read-modify-write so the canonical `full_json` stays in sync.
        rec = await self.read(proposal_id)
        if rec is None:
            return False
        for k, v in fields.items():
            if k in ("proposal_id", "created_at"):
                continue
            if hasattr(rec, k):
                setattr(rec, k, v)
        await self.write(rec)
        return True

    async def count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM learning_proposals")
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "LearningStore",
    "InMemoryLearningStore",
    "SQLiteLearningStore",
]
