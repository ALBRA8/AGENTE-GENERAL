"""MEM-QUALITY — Real Information-Retrieval evaluation of the memory subsystem.

This is a **deterministic integration evaluation**, NOT a real LLM quality
benchmark. It exercises the full memory pipeline (store -> recall ->
retrieve -> rank -> format -> inject) using the always-on Mock provider
plus a "smart" mock that simulates consuming the injected context.

The original `test_memory_evaluation.py` measured memory with binary
``has_info`` flags. This module replaces that with proper IR metrics:

  * recall@k       — of the relevant memories that SHOULD be retrieved,
                       how many are in the top-k? (k=5)
  * precision@k    — of the top-k retrieved, how many are relevant?
  * mrr            — Mean Reciprocal Rank of the first relevant memory
  * distractor_count   — irrelevant memories leaked into top-k
  * isolation_violations — cross-user / cross-context memories surfaced
  * context_chars_added  — chars injected into the SYSTEM DATA block
  * latency_ms     — wall-clock time for retrieve() + format_for_context()
  * contradiction_handling — both sides of a contradiction surfaced (no
                              auto-resolution)
  * no_memory_baseline — same task against an empty store should score 0

Every metric is computed from the MemoryRetriever's output (the same
object the agent loop's REASON phase uses). The retriever enforces
isolation, lifecycle, scoring, top-k, and char-budget — so the metrics
reflect the REAL behavior the agent would observe.

Labelling: results printed by ``test_eval_comprehensive_report`` carry
the header ``DETERMINISTIC INTEGRATION EVALUATION (MOCK PROVIDER)`` so
no one mistakes them for real LLM quality numbers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest

from albra_seed.core.base import MemoryRecord
from albra_seed.core.identity import AgentIdentity
from albra_seed.core.types import (
    AgentResponse,
    AutonomyLevel,
    MemoryType,
    Message,
    Role,
    RunContext,
    Session,
    new_id,
    utc_now,
)
from albra_seed.feedback.manager import FeedbackManager
from albra_seed.feedback.types import FeedbackType
from albra_seed.memory import (
    DecisionMemory,
    DecisionRecord,
    InMemoryMemoryStore,
    MemoryManager,
    MemoryRetriever,
    RetrievalConfig,
)
from albra_seed.providers.mock import MockProvider
from albra_seed.runtime import Runtime
from albra_seed.runtime.runtime import COMPONENT_MEMORY_MANAGER

# --------------------------------------------------------------------------- #
# A "smart" mock provider — simulates consuming the retrieved context.
# --------------------------------------------------------------------------- #


class _SmartMockProvider(MockProvider):
    """Mock provider that simulates using the retrieved context.

    Extends the always-on MockProvider. On every ``complete()``:

      1. Captures the full messages list (for test inspection).
      2. Finds the SYSTEM message containing the ``[RETRIEVED MEMORIES]``
         block that ``MemoryRetriever.format_for_context()`` emits.
      3. Parses each memory line ``- [TYPE, confidence=X.XX, source=Y]
         <content>`` into a content string.
      4. Picks the memory whose content has the highest keyword overlap
         with the user's query (deterministic lexical signal).
      5. Returns that content as the response (or a placeholder when
         no memory matches).

    This is deterministic: same input -> same response. No external LLM.
    It is NOT a real model — but it exercises the full memory pipeline
    (store -> recall -> retrieve -> rank -> format -> inject -> provider)
    so we can measure end-to-end behaviour without an API key.
    """

    name = "smart_mock"
    is_mock = True
    is_external = False

    def __init__(self) -> None:
        super().__init__()
        self.received_messages: list[list[Message]] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        context: RunContext | None = None,
    ) -> AgentResponse:
        self._call_count += 1
        self.received_messages.append(list(messages))

        retrieved_block = ""
        user_text = ""
        for msg in messages:
            if msg.role == Role.SYSTEM and "[RETRIEVED MEMORIES" in msg.content:
                retrieved_block = msg.content
            elif msg.role == Role.USER:
                user_text = msg.content

        memories = self._parse_memories(retrieved_block)
        query_terms = _tokenize(user_text)

        best_match = None
        best_score = 0
        for mem in memories:
            mem_terms = _tokenize(mem)
            if not query_terms:
                continue
            overlap = len(set(query_terms) & set(mem_terms))
            if overlap > best_score:
                best_score = overlap
                best_match = mem

        content = best_match if best_match is not None else "[no relevant memory found]"
        execution_id = context.execution_id if context else new_id("exec")
        correlation_id = context.correlation_id if context else new_id("corr")
        return AgentResponse(
            content=content,
            execution_id=execution_id,
            correlation_id=correlation_id,
            confidence=1.0,
            metadata={
                "provider": self.name,
                "mock": True,
                "best_match_overlap": best_score,
                "parsed_memory_count": len(memories),
            },
        )

    @staticmethod
    def _parse_memories(block: str) -> list[str]:
        """Parse the ``[RETRIEVED MEMORIES ...]`` block into content strings.

        Lines look like::
            - [SEMANTIC, confidence=0.90, source=agent] user prefers Python

        Returns the text after the ``] `` for every entry line.
        """
        if not block:
            return []
        memories: list[str] = []
        for line in block.split("\n"):
            stripped = line.lstrip()
            if not stripped.startswith("- ["):
                continue
            # find the closing "]" of the header
            close = stripped.find("]", 3)
            if close == -1:
                continue
            # content begins 2 chars after the "]"
            content = stripped[close + 2 :].strip()
            if content:
                memories.append(content)
        return memories


# --------------------------------------------------------------------------- #
# IR metrics — pure functions on lists of ids.
# --------------------------------------------------------------------------- #


def _compute_recall_at_k(
    retrieved_ids: list[str], relevant_ids: set[str], k: int = 5
) -> float:
    """recall@k = |relevant ∩ top-k| / |relevant|. 1.0 when nothing relevant."""
    if not relevant_ids:
        return 1.0
    top_k = list(retrieved_ids)[:k]
    return len(set(top_k) & relevant_ids) / len(relevant_ids)


def _compute_precision_at_k(
    retrieved_ids: list[str], relevant_ids: set[str], k: int = 5
) -> float:
    """precision@k = |relevant ∩ top-k| / |top-k|. 0.0 when no retrieval."""
    if not retrieved_ids:
        return 0.0
    top_k = list(retrieved_ids)[:k]
    if not top_k:
        return 0.0
    return len(set(top_k) & relevant_ids) / len(top_k)


def _compute_mrr(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    """Mean Reciprocal Rank: 1/(rank of first relevant) or 0.0."""
    for i, rid in enumerate(retrieved_ids):
        if rid in relevant_ids:
            return 1.0 / (i + 1)
    return 0.0


def _compute_distractor_count(
    retrieved_ids: list[str], relevant_ids: set[str], k: int = 5
) -> int:
    """Irrelevant memories that leaked into top-k."""
    top_k = list(retrieved_ids)[:k]
    return len(set(top_k) - relevant_ids)


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens — mirrors MemoryRetriever._tokenize."""
    if not text:
        return []
    for ch in ".,;:!?'\"()[]{}/\\<>*&^%$#@~`|+=\t\r\n":
        text = text.replace(ch, " ")
    return [t for t in text.lower().split(" ") if t]


# --------------------------------------------------------------------------- #
# Task-result container.
# --------------------------------------------------------------------------- #


@dataclass
class EvalMetrics:
    """One task's measured outcome."""

    task: str
    recall_at_5: float = 0.0
    precision_at_5: float = 0.0
    mrr: float = 0.0
    distractor_count: int = 0
    isolation_violations: int = 0
    context_chars_added: int = 0
    latency_ms: float = 0.0
    retrieved_count: int = 0
    contradiction_handling: bool = False
    notes: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "recall@5": self.recall_at_5,
            "precision@5": self.precision_at_5,
            "mrr": self.mrr,
            "distract": self.distractor_count,
            "iso_viol": self.isolation_violations,
            "ctx_chars": self.context_chars_added,
            "latency_ms": self.latency_ms,
            "retrieved": self.retrieved_count,
            "contra": self.contradiction_handling,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# Eval harness.
# --------------------------------------------------------------------------- #


_DEFAULT_AGENT_ID = "eval-agent"
_DEFAULT_DOMAIN = "default"


async def _seed_memory(
    mgr: MemoryManager,
    *,
    agent_id: str,
    domain: str,
    user_id: str,
    seed: dict[str, Any],
) -> MemoryRecord:
    """Write one memory seed dict through MemoryManager.remember().

    Honours an optional ``created_at_offset_seconds`` (negative = older)
    by mutating the persisted record's ``created_at`` after the write —
    needed for the outdated-vs-recent task where the recency signal must
    differ between two memories by days, not microseconds.
    """
    record = await mgr.remember(
        agent_id=agent_id,
        domain=domain,
        user_id=user_id,
        context_id=seed.get("context_id"),
        memory_type=seed["memory_type"],
        content=seed["content"],
        source=seed.get("source", "test"),
        source_type=seed.get("source_type", "agent"),
        confidence=seed.get("confidence", 0.5),
        is_fact=seed.get("is_fact", False),
    )
    offset = seed.get("created_at_offset_seconds")
    if offset:
        record.created_at = utc_now() + timedelta(seconds=float(offset))
        await mgr.store.write(record)
    return record


async def _run_eval_task(
    task_name: str,
    store_seeds: list[dict[str, Any]],
    query: str,
    *,
    user_id: str = "alice",
    context_id: str | None = None,
    relevant_ids: set[str] | None = None,
    expect_contradiction: bool = False,
    isolation_check_user_id: str | None = None,
    retrieval_config: RetrievalConfig | None = None,
) -> tuple[EvalMetrics, list[Any]]:
    """Run a single eval task. Returns (metrics, retrieved_records).

    The metrics are computed from the MemoryRetriever's output. We also
    call ``retriever.format_for_context(...)`` and measure its char
    length so ``context_chars_added`` reflects what would actually be
    injected into the agent's SYSTEM block.

    ``retrieval_config`` lets a task tune the retriever's cutoff / top-k
    — most tasks use the default config, but task 09 (no-relevant-
    memories) uses a stricter ``min_score`` so the eval can verify
    "unrelated query → empty retrieval" against a small synthetic store
    where the structural baseline alone would otherwise pass the very
    permissive default ``min_score=0.05`` cutoff.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    actual_relevant_ids: set[str] = set()
    for seed in store_seeds:
        seed_user_id = seed.get("user_id", user_id)
        record = await _seed_memory(
            mgr,
            agent_id=_DEFAULT_AGENT_ID,
            domain=_DEFAULT_DOMAIN,
            user_id=seed_user_id,
            seed=seed,
        )
        if seed.get("relevant"):
            actual_relevant_ids.add(record.record_id)

    # Allow the caller to override relevant_ids (e.g. when the relevant
    # memory is created out-of-band by DecisionMemory rather than the
    # manager's remember()).
    relevant_ids = relevant_ids or actual_relevant_ids

    retriever = (
        MemoryRetriever(mgr, config=retrieval_config)
        if retrieval_config is not None
        else MemoryRetriever(mgr)
    )

    t0 = time.perf_counter()
    retrieved = await retriever.retrieve(
        agent_id=_DEFAULT_AGENT_ID,
        query=query,
        domain=_DEFAULT_DOMAIN,
        user_id=user_id,
        context_id=context_id,
    )
    latency_ms = (time.perf_counter() - t0) * 1000.0

    # Time the format step too — it's part of the cost paid before the
    # provider sees the SYSTEM block.
    fmt_t0 = time.perf_counter()
    formatted = retriever.format_for_context(retrieved)
    latency_ms += (time.perf_counter() - fmt_t0) * 1000.0

    retrieved_ids = [r.record.record_id for r in retrieved]

    recall = _compute_recall_at_k(retrieved_ids, relevant_ids, k=5)
    precision = _compute_precision_at_k(retrieved_ids, relevant_ids, k=5)
    mrr = _compute_mrr(retrieved_ids, relevant_ids)
    distractor_count = _compute_distractor_count(retrieved_ids, relevant_ids, k=5)
    context_chars = len(formatted)

    # Isolation violations: count retrieved records whose user_id differs
    # from the querying user_id (or, if isolation_check_user_id is given,
    # records whose user_id != isolation_check_user_id).
    check_user = isolation_check_user_id or user_id
    isolation_violations = sum(
        1
        for r in retrieved
        if r.record.user_id is not None and r.record.user_id != check_user
    )

    contradiction_handling = False
    if expect_contradiction:
        # All relevant_ids must appear in retrieved (no auto-resolution).
        contradiction_handling = relevant_ids.issubset(set(retrieved_ids))

    metrics = EvalMetrics(
        task=task_name,
        recall_at_5=recall,
        precision_at_5=precision,
        mrr=mrr,
        distractor_count=distractor_count,
        isolation_violations=isolation_violations,
        context_chars_added=context_chars,
        latency_ms=latency_ms,
        retrieved_count=len(retrieved),
        contradiction_handling=contradiction_handling,
    )
    return metrics, retrieved


# --------------------------------------------------------------------------- #
# The 10 IR evaluation tasks.
# --------------------------------------------------------------------------- #


async def test_eval_01_preference_recall() -> None:
    """Task 1: store 'user prefers Python', query 'what language?' -> recall=1.0"""
    metrics, _ = await _run_eval_task(
        "01_preference_recall",
        store_seeds=[
            {
                "content": "user prefers Python",
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.9,
                "relevant": True,
            }
        ],
        query="what language does user prefer",
    )
    assert metrics.recall_at_5 == 1.0, (
        f"expected recall@5=1.0, got {metrics.recall_at_5}"
    )
    assert metrics.precision_at_5 == 1.0
    assert metrics.distractor_count == 0
    assert metrics.isolation_violations == 0
    assert metrics.context_chars_added > 0


async def test_eval_02_paraphrased_query() -> None:
    """Task 2: store 'user likes Python programming', query 'favorite
    programming language?' -> recall >= 0.5 (lexical match on 'programming')."""
    metrics, _ = await _run_eval_task(
        "02_paraphrased_query",
        store_seeds=[
            {
                "content": "user likes Python programming",
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.8,
                "relevant": True,
            }
        ],
        query="favorite programming language",
    )
    assert metrics.recall_at_5 >= 0.5, (
        f"expected recall@5>=0.5 for paraphrased query, got {metrics.recall_at_5}"
    )
    assert metrics.isolation_violations == 0


async def test_eval_03_distractor_filtering() -> None:
    """Task 3: 1 relevant + 10 distractors -> relevant is in top-k,
    distractor_count is bounded, recall@5 = 1.0."""
    seeds: list[dict[str, Any]] = [
        {
            "content": f"noise topic {i} unrelated to language preference",
            "memory_type": MemoryType.EPISODIC,
            "confidence": 0.3,
            "relevant": False,
        }
        for i in range(10)
    ]
    seeds.append(
        {
            "content": "user prefers Python",
            "memory_type": MemoryType.SEMANTIC,
            "confidence": 0.9,
            "relevant": True,
        }
    )
    metrics, _ = await _run_eval_task(
        "03_distractor_filtering",
        store_seeds=seeds,
        query="what language does user prefer",
    )
    assert metrics.recall_at_5 == 1.0, (
        f"expected recall@5=1.0 (relevant memory must be retrievable "
        f"amid 10 distractors), got {metrics.recall_at_5}"
    )
    # precision@5 is at least 1/5 (one relevant among up to 5 retrieved).
    assert metrics.precision_at_5 >= 0.2, (
        f"expected precision@5>=0.2 (>=1 of 5 relevant), "
        f"got {metrics.precision_at_5}"
    )
    assert metrics.isolation_violations == 0


async def test_eval_04_contradiction_pair() -> None:
    """Task 4: two contradicting memories -> BOTH retrieved (no auto-resolution)."""
    metrics, retrieved = await _run_eval_task(
        "04_contradiction_pair",
        store_seeds=[
            {
                "content": "user likes Python",
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.7,
                "relevant": True,
            },
            {
                "content": "user hates Python",
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.7,
                "relevant": True,
            },
        ],
        query="Python preference",
        expect_contradiction=True,
    )
    assert metrics.retrieved_count >= 2, (
        f"expected >=2 retrieved for contradiction pair, "
        f"got {metrics.retrieved_count}"
    )
    assert metrics.contradiction_handling is True, (
        "expected both contradicting memories to surface (no auto-resolution)"
    )
    # The agent should see BOTH — both sides of the contradiction.
    contents = [str(r.record.content) for r in retrieved]
    assert any("likes" in c for c in contents), (
        f"'likes Python' missing from retrieved: {contents}"
    )
    assert any("hates" in c for c in contents), (
        f"'hates Python' missing from retrieved: {contents}"
    )


async def test_eval_05_outdated_vs_recent() -> None:
    """Task 5: old low-conf + new high-conf -> both retrieved; new ranks first."""
    metrics, retrieved = await _run_eval_task(
        "05_outdated_vs_recent",
        store_seeds=[
            {
                "content": "user uses Python 2",
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.3,
                "relevant": True,
                # Make this one look 30 days old.
                "created_at_offset_seconds": -30 * 24 * 3600,
            },
            {
                "content": "user uses Python 3",
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.95,
                "relevant": True,
                # Recent — leave default created_at.
            },
        ],
        query="Python version",
    )
    assert metrics.recall_at_5 >= 0.5, (
        f"expected recall@5>=0.5 (at least the high-confidence one), "
        f"got {metrics.recall_at_5}"
    )
    assert metrics.retrieved_count >= 1
    # The recent high-confidence memory MUST rank higher than the old one.
    contents = [str(r.record.content) for r in retrieved]
    if "user uses Python 2" in contents and "user uses Python 3" in contents:
        idx_2 = contents.index("user uses Python 2")
        idx_3 = contents.index("user uses Python 3")
        assert idx_3 < idx_2, (
            f"expected 'Python 3' to rank ABOVE 'Python 2' "
            f"(higher confidence + more recent); got order={contents}"
        )


async def test_eval_06_correction_recovery() -> None:
    """Task 6: store a correction FeedbackRecord + a SEMANTIC memory noting
    the correction -> the memory is retrieved when a related query comes.

    The FeedbackManager persists CORRECTION records in its own store
    (separate from MemoryStore). This test verifies the *memory* path:
    a memory describing a correction is retrievable, AND the linked
    FeedbackRecord (CORRECTION type) is queryable via FeedbackManager.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)

    # 1. Store the corrected memory (the thing we want retrievable).
    corrected = await _seed_memory(
        mgr,
        agent_id=_DEFAULT_AGENT_ID,
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
        seed={
            "content": "CORRECTION: earlier we said user prefers Python 2; "
            "actually user prefers Python 3",
            "memory_type": MemoryType.SEMANTIC,
            "confidence": 0.85,
            "relevant": True,
        },
    )

    # 2. Record a CORRECTION feedback linked to this memory's decision
    #    (treat the memory as the corrected decision for the test).
    fb_mgr = FeedbackManager()
    fb_id = await fb_mgr.record(
        agent_id=_DEFAULT_AGENT_ID,
        feedback_type=FeedbackType.CORRECTION,
        evaluation="preference was misremembered as Python 2",
        source="eval-harness",
        source_type="agent",
        confidence=0.9,
        decision_id=corrected.record_id,
    )

    # 3. The correction feedback must exist + be CORRECTION type.
    fb = await fb_mgr.get(fb_id)
    assert fb is not None
    assert fb.feedback_type == FeedbackType.CORRECTION

    # 4. The memory must be retrievable via the retriever.
    retriever = MemoryRetriever(mgr)
    retrieved = await retriever.retrieve(
        agent_id=_DEFAULT_AGENT_ID,
        query="Python preference correction",
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
    )
    assert any(r.record.record_id == corrected.record_id for r in retrieved), (
        "correction memory was not retrieved for a related query"
    )
    assert len(retrieved) >= 1


async def test_eval_07_decision_recall() -> None:
    """Task 7: store a DecisionRecord via DecisionMemory -> the underlying
    MemoryRecord is retrievable when the query mentions the decision topic."""
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    decision_mgr = DecisionMemory(store=store)

    decision = DecisionRecord(
        agent_id=_DEFAULT_AGENT_ID,
        objective="choose a hosting provider for the new service",
        options_considered=[
            {"name": "AWS", "pros": "mature", "cons": "cost"},
            {"name": "GCP", "pros": "ML tooling", "cons": "less mature"},
        ],
        decision_taken="GCP",
        reasoning="ML tooling aligns with the team roadmap",
        confidence=0.8,
    )
    decision_id = await decision_mgr.record_decision(
        decision, domain=_DEFAULT_DOMAIN, user_id="alice"
    )

    # DecisionMemory bypasses MemoryManager.remember() so the lifecycle
    # CREATED event is not recorded. Add one so the retriever treats the
    # record as alive.
    record = await store.read(decision_id)
    assert record is not None
    mgr.lifecycle.record_created(record.record_id, by="agent")

    retriever = MemoryRetriever(mgr)
    retrieved = await retriever.retrieve(
        agent_id=_DEFAULT_AGENT_ID,
        query="hosting provider decision",
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
    )
    assert any(r.record.record_id == decision_id for r in retrieved), (
        "DecisionRecord was not retrievable for a related query"
    )
    # The retrieved record's underlying MemoryRecord must be DECISION type.
    matching = [r for r in retrieved if r.record.record_id == decision_id]
    assert matching[0].record.memory_type == MemoryType.DECISION


async def test_eval_08_inference_not_fact() -> None:
    """Task 8: store an INFERENCE-typed memory with confidence=0.5 ->
    retrieved AND is_fact stays False (no auto-promotion)."""
    metrics, retrieved = await _run_eval_task(
        "08_inference_not_fact",
        store_seeds=[
            {
                # The content describes an INFERENCE, not a verified FACT.
                "content": "inferred: user may prefer Python based on past "
                "queries (low confidence, no evidence)",
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.5,
                "is_fact": False,
                "relevant": True,
            }
        ],
        query="Python preference inference",
    )
    assert metrics.retrieved_count >= 1, "inference memory was not retrieved"
    assert metrics.recall_at_5 == 1.0
    # The retrieved record MUST keep is_fact=False — no auto-promotion.
    for r in retrieved:
        assert r.record.is_fact is False, (
            f"inference memory auto-promoted to is_fact=True: "
            f"{r.record.record_id} content={r.record.content!r}"
        )


async def test_eval_09_no_relevant_memories() -> None:
    """Task 9: query about topic with NO matching memories -> empty
    retrieval, no errors, retrieved_count=0.

    Uses a stricter ``RetrievalConfig(min_score=0.7)`` than the default
    (0.05). The default is intentionally permissive so a small working
    set always returns SOMETHING for the agent to consider; an
    evaluation that wants to assert "no match -> empty result" needs a
    cutoff above the structural baseline (~0.65 for SEMANTIC + recent
    + high-conf + agent source with zero lexical overlap). With
    ``min_score=0.7`` the only-marginal memory is correctly excluded
    and retrieval returns [].
    """
    stricter = RetrievalConfig(min_score=0.7)
    metrics, retrieved = await _run_eval_task(
        "09_no_relevant_memories",
        store_seeds=[
            {
                "content": "user likes Python",
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.9,
                "relevant": False,
            }
        ],
        query="what is the weather today in tokyo",
        retrieval_config=stricter,
    )
    assert metrics.retrieved_count == 0, (
        f"expected 0 memories retrieved for an unrelated query "
        f"under min_score=0.7 (lexical cutoff should exclude "
        f"'user likes Python'), got {metrics.retrieved_count}: "
        f"{[str(r.record.content) for r in retrieved]}"
    )
    # No exception should have propagated; retrieved is just empty.
    assert isinstance(retrieved, list)


async def test_eval_10_isolation_no_leak() -> None:
    """Task 10: user A and user B have memories -> A's query returns 0 B records."""
    metrics, retrieved = await _run_eval_task(
        "10_isolation_no_leak",
        store_seeds=[
            {
                "content": "secret of alice that should never leak",
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.9,
                "user_id": "alice",
                "relevant": True,
            },
            {
                "content": "secret of bob that should never leak",
                "memory_type": MemoryType.SEMANTIC,
                "confidence": 0.9,
                "user_id": "bob",
                "relevant": False,
            },
        ],
        query="secret",
        user_id="alice",
        isolation_check_user_id="alice",
    )
    assert metrics.isolation_violations == 0, (
        f"isolation violated: {metrics.isolation_violations} cross-user "
        f"records leaked into alice's results"
    )
    contents = [str(r.record.content) for r in retrieved]
    assert all("bob" not in c for c in contents), (
        f"BOB's secret leaked into alice's results: {contents}"
    )
    assert any("alice" in c for c in contents), (
        f"alice's own memory missing from her results: {contents}"
    )


# --------------------------------------------------------------------------- #
# Latency, no-memory baseline, and full-pipeline tests.
# --------------------------------------------------------------------------- #


async def test_eval_latency_under_500ms() -> None:
    """Latency: retrieve() + format_for_context() on a 100-record store
    must complete in under 500ms (deterministic, in-memory)."""
    seeds: list[dict[str, Any]] = [
        {
            "content": f"memory record number {i} about topic {i % 7}",
            "memory_type": MemoryType.EPISODIC,
            "confidence": 0.5,
            "relevant": False,
        }
        for i in range(100)
    ]
    metrics, _ = await _run_eval_task(
        "latency_check", store_seeds=seeds, query="topic 3"
    )
    assert metrics.latency_ms < 500.0, (
        f"latency too high: {metrics.latency_ms:.1f}ms "
        f"(must be <500ms for 100 records, in-memory store)"
    )


async def test_eval_no_memory_baseline() -> None:
    """Baseline: same task with an empty store -> recall=0 precision=0
    (the no-memory path scores nothing)."""
    metrics, _ = await _run_eval_task(
        "no_memory_baseline",
        store_seeds=[],
        query="anything at all",
        relevant_ids={"nonexistent-id"},
    )
    assert metrics.retrieved_count == 0, (
        f"empty store should return 0 memories, got {metrics.retrieved_count}"
    )
    assert metrics.recall_at_5 == 0.0
    assert metrics.precision_at_5 == 0.0
    assert metrics.context_chars_added > 0  # the (none) marker block
    assert metrics.latency_ms >= 0.0


async def test_eval_full_pipeline_smart_provider() -> None:
    """Full pipeline: store -> recall -> retrieve -> rank -> format ->
    inject SYSTEM block -> _SmartMockProvider parses + returns best match.

    Verifies the _SmartMockProvider can actually consume the formatted
    [RETRIEVED MEMORIES] block emitted by MemoryRetriever.format_for_context
    and that the best-match response carries the relevant content.
    """
    runtime = Runtime()
    await runtime.startup()

    # Use the auto-provisioned store + manager from birth(), but
    # pre-populate the store BEFORE running the agent so the REASON phase
    # retrieves our memories.
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    runtime.register_memory_store(_DEFAULT_DOMAIN, store)
    runtime.register(COMPONENT_MEMORY_MANAGER, mgr)

    smart = _SmartMockProvider()
    runtime.register_provider(smart)

    identity = AgentIdentity(
        name=_DEFAULT_AGENT_ID,
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        providers=["smart_mock"],
    )
    # rename so identity.providers matches the provider we registered.
    identity.providers = [smart.name]

    # Seed the memory using the IDENTITY's agent_id (not the constant
    # _DEFAULT_AGENT_ID) — the agent loop queries the store with
    # identity.agent_id, so the memory must land in the matching isolation
    # bucket or retrieval returns nothing.
    await _seed_memory(
        mgr,
        agent_id=identity.agent_id,
        domain=_DEFAULT_DOMAIN,
        user_id="alice",
        seed={
            "content": "user prefers Python over JavaScript for backend work",
            "memory_type": MemoryType.SEMANTIC,
            "confidence": 0.9,
            # Match the session's context_id so the memory lands in the
            # same isolation bucket the agent's REASON phase will query.
            "context_id": "ctx-eval",
        },
    )

    from albra_seed.runtime.agent import Agent

    session = Session(
        agent_id=identity.agent_id,
        user_id="alice",
        context_id="ctx-eval",
        domain=_DEFAULT_DOMAIN,
    )
    agent = Agent(identity, runtime)
    response = await agent.run(
        "what programming language does the user prefer?", session=session
    )

    # The smart provider must have been called exactly once.
    assert smart.call_count == 1, (
        f"expected provider called once, got {smart.call_count}"
    )
    msgs = smart.received_messages[0]

    # The SYSTEM block containing the [RETRIEVED MEMORIES] header must
    # have been injected.
    system_blobs = [m.content for m in msgs if m.role == Role.SYSTEM]
    assert any("[RETRIEVED MEMORIES" in b for b in system_blobs), (
        "REASON phase did not inject the [RETRIEVED MEMORIES] SYSTEM block"
    )

    # The smart provider's response must mention Python (the relevant
    # memory's content surfaced as the best match).
    assert "Python" in response.content, (
        f"smart provider did not surface the relevant memory: "
        f"response={response.content!r}"
    )
    await runtime.shutdown()


# --------------------------------------------------------------------------- #
# Comprehensive report — prints a metrics table; does NOT assert.
# --------------------------------------------------------------------------- #


async def test_eval_comprehensive_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run all 10 tasks + the baseline, print a markdown metrics table.

    This test does NOT assert — its sole purpose is to surface the
    evaluation results in the CI / pytest output. The per-task tests
    above carry the assertions; this test carries the visibility.

    The header explicitly labels the results as
    "DETERMINISTIC INTEGRATION EVALUATION (MOCK PROVIDER)" so no one
    mistakes them for real LLM quality numbers.
    """
    task_specs: list[dict[str, Any]] = [
        {
            "name": "01_preference_recall",
            "seeds": [
                {
                    "content": "user prefers Python",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.9,
                    "relevant": True,
                }
            ],
            "query": "what language does user prefer",
            "expect_contradiction": False,
        },
        {
            "name": "02_paraphrased_query",
            "seeds": [
                {
                    "content": "user likes Python programming",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.8,
                    "relevant": True,
                }
            ],
            "query": "favorite programming language",
            "expect_contradiction": False,
        },
        {
            "name": "03_distractor_filtering",
            "seeds": [
                {
                    "content": f"noise topic {i}",
                    "memory_type": MemoryType.EPISODIC,
                    "confidence": 0.3,
                    "relevant": False,
                }
                for i in range(10)
            ]
            + [
                {
                    "content": "user prefers Python",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.9,
                    "relevant": True,
                }
            ],
            "query": "what language does user prefer",
            "expect_contradiction": False,
        },
        {
            "name": "04_contradiction_pair",
            "seeds": [
                {
                    "content": "user likes Python",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.7,
                    "relevant": True,
                },
                {
                    "content": "user hates Python",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.7,
                    "relevant": True,
                },
            ],
            "query": "Python preference",
            "expect_contradiction": True,
        },
        {
            "name": "05_outdated_vs_recent",
            "seeds": [
                {
                    "content": "user uses Python 2",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.3,
                    "relevant": True,
                    "created_at_offset_seconds": -30 * 24 * 3600,
                },
                {
                    "content": "user uses Python 3",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.95,
                    "relevant": True,
                },
            ],
            "query": "Python version",
            "expect_contradiction": False,
        },
        {
            "name": "06_correction_recovery",
            "seeds": [
                {
                    "content": "CORRECTION: user prefers Python 3, not Python 2",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.85,
                    "relevant": True,
                }
            ],
            "query": "Python preference correction",
            "expect_contradiction": False,
        },
        {
            "name": "07_decision_recall",
            "seeds": [
                {
                    "content": "DECISION objective=hosting decision_taken=GCP",
                    "memory_type": MemoryType.DECISION,
                    "confidence": 0.8,
                    "relevant": True,
                }
            ],
            "query": "hosting provider decision",
            "expect_contradiction": False,
        },
        {
            "name": "08_inference_not_fact",
            "seeds": [
                {
                    "content": "inferred: user may prefer Python (low confidence)",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.5,
                    "is_fact": False,
                    "relevant": True,
                }
            ],
            "query": "Python preference inference",
            "expect_contradiction": False,
        },
        {
            "name": "09_no_relevant_memories",
            "seeds": [
                {
                    "content": "user likes Python",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.9,
                    "relevant": False,
                }
            ],
            "query": "what is the weather today in tokyo",
            "expect_contradiction": False,
            # Use the same stricter cutoff as the per-task test so the
            # report's metrics match what the assertion verifies.
            "retrieval_config": RetrievalConfig(min_score=0.7),
        },
        {
            "name": "10_isolation_no_leak",
            "seeds": [
                {
                    "content": "secret of alice that should never leak",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.9,
                    "user_id": "alice",
                    "relevant": True,
                },
                {
                    "content": "secret of bob that should never leak",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.9,
                    "user_id": "bob",
                    "relevant": False,
                },
            ],
            "query": "secret",
            "user_id": "alice",
            "expect_contradiction": False,
        },
    ]

    results: list[EvalMetrics] = []
    for spec in task_specs:
        metrics, _ = await _run_eval_task(
            spec["name"],
            store_seeds=spec["seeds"],
            query=spec["query"],
            user_id=spec.get("user_id", "alice"),
            expect_contradiction=spec.get("expect_contradiction", False),
            isolation_check_user_id=spec.get("user_id", "alice"),
            retrieval_config=spec.get("retrieval_config"),
        )
        results.append(metrics)

    # Baseline: empty store.
    baseline, _ = await _run_eval_task(
        "00_no_memory_baseline",
        store_seeds=[],
        query="anything",
        relevant_ids={"nonexistent-id"},
    )
    results.insert(0, baseline)

    # Print the report. capsys is the canonical pytest capture fixture;
    # the print goes to stdout so `-s` reveals it.
    print("\n" + "=" * 92)
    print("DETERMINISTIC INTEGRATION EVALUATION (MOCK PROVIDER)")
    print("NOT a real LLM quality benchmark — synthetic data, in-memory store.")
    print("=" * 92)
    header = (
        f"{'task':<28} {'recall@5':>9} {'prec@5':>8} {'mrr':>6} "
        f"{'distract':>9} {'iso_viol':>9} {'ctx_chars':>10} "
        f"{'lat_ms':>8} {'retr':>5} {'contra':>7}"
    )
    print(header)
    print("-" * 92)
    for r in results:
        print(
            f"{r.task:<28} {r.recall_at_5:>9.2f} {r.precision_at_5:>8.2f} "
            f"{r.mrr:>6.2f} {r.distractor_count:>9} {r.isolation_violations:>9} "
            f"{r.context_chars_added:>10} {r.latency_ms:>8.1f} "
            f"{r.retrieved_count:>5} {str(r.contradiction_handling):>7}"
        )
    print("=" * 92)
    print(
        "NOTE: metrics reflect MemoryRetriever + InMemoryMemoryStore behaviour "
        "only.\n"
        "      The Mock provider parses the formatted SYSTEM block but does "
        "not 'reason'.\n"
        "      For real LLM quality evaluation, swap MockProvider for a live "
        "provider and\n"
        "      re-run with human-judged relevance labels."
    )

    # Sanity: at least the baseline is present and matches the contract.
    assert baseline.task == "00_no_memory_baseline"
    assert baseline.recall_at_5 == 0.0
    assert baseline.retrieved_count == 0
    # And every task produced a metric row.
    assert len(results) == len(task_specs) + 1


# --------------------------------------------------------------------------- #
# Optional: write the report to a file so CI artifacts persist it.
# --------------------------------------------------------------------------- #


async def test_eval_writes_report_artifact(tmp_path: Any) -> None:
    """Write the comprehensive report to a markdown file under tmp_path.

    Ensures the evaluation numbers survive as a CI artifact even when
    pytest is run with output capture (no -s).
    """
    metrics_list: list[EvalMetrics] = []
    # Re-use a minimal subset for the artifact (avoid duplicating the
    # whole suite — the comprehensive_report test already exercises all).
    for spec in [
        {
            "name": "01_preference_recall",
            "seeds": [
                {
                    "content": "user prefers Python",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.9,
                    "relevant": True,
                }
            ],
            "query": "what language does user prefer",
        },
        {
            "name": "10_isolation_no_leak",
            "seeds": [
                {
                    "content": "secret of alice",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.9,
                    "user_id": "alice",
                    "relevant": True,
                },
                {
                    "content": "secret of bob",
                    "memory_type": MemoryType.SEMANTIC,
                    "confidence": 0.9,
                    "user_id": "bob",
                    "relevant": False,
                },
            ],
            "query": "secret",
            "user_id": "alice",
        },
    ]:
        m, _ = await _run_eval_task(
            spec["name"],
            store_seeds=spec["seeds"],
            query=spec["query"],
            user_id=spec.get("user_id", "alice"),
            isolation_check_user_id=spec.get("user_id", "alice"),
        )
        metrics_list.append(m)

    out = tmp_path / "memory_eval_report.md"
    lines = [
        "# Memory Quality Evaluation Report",
        "",
        "**DETERMINISTIC INTEGRATION EVALUATION (MOCK PROVIDER)**",
        "",
        "Not a real LLM quality benchmark. Synthetic data, in-memory store.",
        "",
        "| task | recall@5 | precision@5 | mrr | distractors | iso_viol |"
        " ctx_chars | latency_ms | retrieved |",
        "|------|---------:|------------:|----:|------------:|---------:|"
        "----------:|-----------:|----------:|",
    ]
    for r in metrics_list:
        lines.append(
            f"| {r.task} | {r.recall_at_5:.2f} | {r.precision_at_5:.2f} | "
            f"{r.mrr:.2f} | {r.distractor_count} | {r.isolation_violations} | "
            f"{r.context_chars_added} | {r.latency_ms:.1f} | "
            f"{r.retrieved_count} |"
        )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert out.exists()
    assert "DETERMINISTIC" in out.read_text(encoding="utf-8")


__all__ = [
    "_SmartMockProvider",
    "_compute_recall_at_k",
    "_compute_precision_at_k",
    "_compute_mrr",
    "_compute_distractor_count",
    "_run_eval_task",
    "EvalMetrics",
]
