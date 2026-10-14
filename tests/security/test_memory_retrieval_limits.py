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
def test_retrieval_config_rejects_invalid_or_unbounded_limits(
    field: str, value: int
) -> None:
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

    assert await retriever.retrieve(
        agent_id="agent-test",
        query="bounded retrieval",
        limit=10_000,
    ) == []
