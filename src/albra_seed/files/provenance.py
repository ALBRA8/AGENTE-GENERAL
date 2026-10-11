"""File provenance — tamper-evident record of file origin + modifications.

Each modification creates a new `ProvenanceEntry` hash-chained over the
canonical JSON of (file path, hash, action, actor, timestamp,
prev_hash). Tampering with any entry invalidates the chain from that
point onward.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now
from albra_seed.files.metadata import content_hash


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _hash_str(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ProvenanceEntry(BaseModel):
    """A single tamper-evident provenance entry."""

    entry_id: str = Field(default_factory=lambda: new_id("prov-entry"))
    file_path: str
    hash: str  # content hash of the file at this entry
    action: str  # create | modify | delete | read
    actor: str = "system"
    timestamp: datetime = Field(default_factory=utc_now)
    prev_hash: str = ""
    entry_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def compute_hash(self) -> str:
        payload = _canonical_json(
            {
                "entry_id": self.entry_id,
                "file_path": self.file_path,
                "hash": self.hash,
                "action": self.action,
                "actor": self.actor,
                "timestamp": self.timestamp.isoformat(),
                "prev_hash": self.prev_hash,
            }
        )
        return _hash_str(payload)

    def seal(self, prev_hash: str = "") -> str:
        self.prev_hash = prev_hash
        self.entry_hash = self.compute_hash()
        return self.entry_hash

    def verify(self) -> bool:
        if not self.entry_hash:
            return False
        return self.compute_hash() == self.entry_hash


class FileProvenance:
    """Append-only provenance chain for a single file (or path)."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self._entries: list[ProvenanceEntry] = []

    def record(
        self,
        *,
        action: str,
        content: bytes | str | None = None,
        actor: str = "system",
        metadata: dict[str, Any] | None = None,
    ) -> ProvenanceEntry:
        # `content=None` is allowed for `delete` actions where there is no
        # content to hash; we record the empty-string hash in that case.
        h = content_hash(content) if content is not None else content_hash(b"")
        prev = self._entries[-1].entry_hash if self._entries else ""
        entry = ProvenanceEntry(
            file_path=self.file_path,
            hash=h,
            action=action,
            actor=actor,
            metadata=metadata or {},
        )
        entry.seal(prev_hash=prev)
        self._entries.append(entry)
        return entry

    def entries(self) -> list[ProvenanceEntry]:
        return list(self._entries)

    def latest_hash(self) -> str | None:
        return self._entries[-1].hash if self._entries else None

    def verify_chain(self) -> bool:
        prev = ""
        for entry in self._entries:
            if entry.prev_hash != prev:
                return False
            if not entry.verify():
                return False
            prev = entry.entry_hash
        return True

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["FileProvenance", "ProvenanceEntry"]
