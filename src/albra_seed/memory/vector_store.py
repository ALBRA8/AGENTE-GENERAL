"""Vector memory store — optional, requires chromadb.

Lazy-imported. Raises ImportError with a clear message if chromadb is not
installed. This keeps the default stack dependency-free.
"""

from __future__ import annotations

from typing import Any

from albra_seed.core.base import MemoryRecord, MemoryStore
from albra_seed.core.types import MemoryType


class VectorMemoryStore(MemoryStore):
    """Optional ChromaDB-backed memory store.

    Construction lazy-imports chromadb; raises ImportError if missing.
    Used for similarity search over semantic memories. Falls back gracefully
    when not installed — callers should catch ImportError and use an
    alternative store.
    """

    def __init__(
        self,
        *,
        collection_name: str = "albra_memories",
        persist_path: str | None = None,
        embedding_fn: Any = None,
    ) -> None:
        try:
            import chromadb  # type: ignore  # noqa: F401
        except ImportError as e:  # pragma: no cover - exercised when chromadb missing
            raise ImportError(
                "VectorMemoryStore requires 'chromadb'. "
                "Install it via `pip install chromadb` or use SQLiteMemoryStore."
            ) from e
        import chromadb  # type: ignore

        client_kwargs: dict[str, Any] = {}
        if persist_path is not None:
            self._client = chromadb.PersistentClient(path=persist_path)
        else:
            self._client = chromadb.Client(**client_kwargs)
        self._collection = self._client.get_or_create_collection(name=collection_name)
        self._embedding_fn = embedding_fn
        self._records: dict[str, MemoryRecord] = {}

    async def write(self, record: MemoryRecord) -> str:
        document = (
            record.content if isinstance(record.content, str) else str(record.content)
        )
        meta = {
            "agent_id": record.agent_id,
            "domain": record.domain,
            "user_id": record.user_id or "",
            "context_id": record.context_id or "",
            "memory_type": record.memory_type.value,
            "confidence": float(record.confidence),
            "is_fact": int(bool(record.is_fact)),
        }
        self._collection.upsert(
            ids=[record.record_id],
            documents=[document],
            metadatas=[meta],
        )
        self._records[record.record_id] = record
        return record.record_id

    async def read(self, record_id: str) -> MemoryRecord | None:
        return self._records.get(record_id)

    async def query(
        self,
        *,
        agent_id: str,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        memory_type: MemoryType | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        where: dict[str, Any] = {
            "agent_id": agent_id,
            "domain": domain,
            "user_id": user_id or "",
            "context_id": context_id or "",
        }
        if memory_type is not None:
            where["memory_type"] = memory_type.value
        res = self._collection.get(where=where, limit=int(limit))
        ids = res.get("ids", []) if isinstance(res, dict) else []
        return [self._records[i] for i in ids if i in self._records]

    async def delete(self, record_id: str) -> bool:
        if record_id not in self._records:
            return False
        self._collection.delete(ids=[record_id])
        del self._records[record_id]
        return True

    async def count(self, *, agent_id: str, domain: str = "default") -> int:
        return sum(
            1
            for r in self._records.values()
            if r.agent_id == agent_id and r.domain == domain
        )

    async def search(
        self,
        query: str,
        *,
        agent_id: str,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        n_results: int = 5,
    ) -> list[MemoryRecord]:
        where: dict[str, Any] = {
            "agent_id": agent_id,
            "domain": domain,
            "user_id": user_id or "",
            "context_id": context_id or "",
        }
        res = self._collection.query(
            query_texts=[query],
            n_results=int(n_results),
            where=where,
        )
        ids = (res.get("ids") or [[]])[0]
        return [self._records[i] for i in ids if i in self._records]
