from __future__ import annotations

import pytest

from albra_seed.memory import InMemoryMemoryStore, MemoryManager, MemoryRetriever, RetrievalConfig


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("top_k", 0),
        ("top_k", 1_001),
        ("candidate_limit", 0),
        ("candidate_limit", 10_001),
        ("max_context_chars", 0),
        ("max_context_chars", 1_000_001),
    ],
)
def test_retrieval_config_rejects_invalid_or_unbounded_limits(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        RetrievalConfig(**{field: value})


@pytest.mark.parametrize("limit", [0, -1, 10_001, True, 1.5])
async def test_retriever_rejects_invalid_limit_overrides(limit: object) -> None:
    retriever = MemoryRetriever(MemoryManager(store=InMemoryMemoryStore()))

    with pytest.raises(ValueError, match="retrieval limit"):
        await retriever.retrieve(
            agent_id="agent-test",
            query="bounded retrieval",
            limit=limit,  # type: ignore[arg-type]
        )


async def test_retriever_accepts_maximum_bounded_limit() -> None:
    retriever = MemoryRetriever(MemoryManager(store=InMemoryMemoryStore()))

    assert (
        await retriever.retrieve(
            agent_id="agent-test",
            query="bounded retrieval",
            limit=10_000,
        )
        == []
    )

async def test_retriever_withholds_memory_when_lifecycle_history_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = MemoryManager(store=InMemoryMemoryStore())
    record = await manager.remember(
        agent_id="agent-test",
        content="forgotten private memory",
    )
    retriever = MemoryRetriever(manager)

    def broken_history(_: str) -> list[object]:
        raise RuntimeError("lifecycle storage unavailable")

    monkeypatch.setattr(manager.lifecycle, "history", broken_history)
    results = await retriever.retrieve(
        agent_id="agent-test",
        query="private memory",
    )

    assert results == []
    assert all(item.record.record_id != record.record_id for item in results)
