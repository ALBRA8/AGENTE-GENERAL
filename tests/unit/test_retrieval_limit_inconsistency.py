"""VERIFY-RETRIEVAL — candidate_limit inconsistency contract tests.

This file pins down the contract of ``RetrievalConfig.candidate_limit``
and locks the 1499+1 regression that motivated its raise from 1000 → 10000.

TRACE (file:line — where the limit is applied):
    1. ``RetrievalConfig.candidate_limit`` default value
       → ``src/albra_seed/memory/retriever.py`` (the field declaration
         on the ``RetrievalConfig`` model).
    2. ``MemoryRetriever.retrieve()`` passes ``limit=candidate_limit`` to
       ``manager.recall()`` → ``retriever.py`` inside ``retrieve()``
       (the ``candidate_limit = limit if limit is not None else
       self._config.candidate_limit`` block, then the
       ``candidates = await self._manager.recall(..., limit=candidate_limit)``
       call).
    3. ``MemoryManager.recall()`` forwards ``limit=limit`` to
       ``store.query()`` → ``src/albra_seed/memory/manager.py``
       (``recall()``'s ``await self.store.query(..., limit=limit)``).
    4. ``store.query()`` applies the limit BEFORE ranking:
         - SQLite: ``ORDER BY created_at DESC LIMIT ?`` (see
           ``sqlite_store.py`` — the SQL string built in ``query()``).
         - InMemory: ``matched.sort(key=_created_at_sort_key, reverse=True)``
           then ``matched[:limit]`` (see ``inmemory_store.py``).
    5. Ranking happens in the retriever, AFTER the store returns:
       scoring loop → ``scored.sort(...)`` → ``min_score`` cutoff →
       ``top_k`` + ``max_context_chars`` truncation (all in ``retrieve()``).

THE INCONSISTENCY (confirmed by the reproduction in
``test_relevant_old_memory_not_excluded_by_candidate_limit``):

    Because the store sorts by ``created_at DESC`` (NOT by score) and
    slices to ``limit`` BEFORE the retriever scores anything, a
    ``candidate_limit`` smaller than the position of a relevant OLD
    memory in that sort order will SILENTLY EXCLUDE it before ranking.

    Concretely: with the historical default of 1000, a store holding
    1499 recent irrelevant records + 1 thirty-day-old relevant record
    (the OLDEST in the store) returned only the 1000 NEWEST — the
    relevant old memory never reached the scorer, so it could NEVER be
    retrieved no matter how well it matched the query.

THE FIX (Option A — increase the default):

    The default ``candidate_limit`` was raised from 1000 → 10000. The
    limit is INTENTIONAL — it bounds the per-call materialisation cost
    (a fully unbounded scan on a 100k-record store would build 100k
    MemoryRecord objects per retrieval). 10000 covers most practical
    working sets; callers with larger stores who care about OLD
    memories raise ``candidate_limit`` explicitly. Option B (remove the
    limit entirely) was rejected because it sacrifices the scan-cost
    bound. Option C (push scoring into the store) was rejected as too
    invasive for a Phase-2 hardening fix.

    The three tests below pin the contract:
      1. The 1499+1 regression (default config MUST retrieve the OLD
         relevant memory).
      2. The default-size assertion (``candidate_limit`` >= 1500 by
         default, so the 1500-record scenario always gets a chance to
         be ranked).
      3. The bounded-scan assertion (``candidate_limit=10`` DOES
         truncate the candidate pool to 10 — i.e. the limit is
         enforced, not a no-op, and the known limitation is opt-in via
         a low explicit limit).

CRITICAL: every test runs with NO external credentials. All async —
pytest is configured with ``asyncio_mode = "auto"``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from albra_seed.core.base import MemoryRecord, MemoryStore
from albra_seed.core.types import MemoryType, utc_now
from albra_seed.memory import (
    InMemoryMemoryStore,
    MemoryManager,
    MemoryRetriever,
    RetrievalConfig,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_record(
    *,
    record_id: str,
    memory_type: MemoryType,
    content: str,
    confidence: float,
    created_at: Any,
    source_type: str = "agent",
    evidence_ids: list[str] | None = None,
    is_fact: bool = False,
    user_id: str | None = "alice",
) -> MemoryRecord:
    """Build a MemoryRecord directly — bypasses ``remember()`` provenance /
    lifecycle overhead so we can write 1500 records fast and with
    deterministic ``created_at`` timestamps.

    The retriever's ``_is_alive()`` returns True for records with no
    lifecycle history (no dead events → alive), so direct store writes
    are safe for retrieval tests.
    """
    return MemoryRecord(
        record_id=record_id,
        agent_id="agent-A",
        domain="default",
        user_id=user_id,
        context_id=None,
        memory_type=memory_type,
        content=content,
        confidence=confidence,
        source=source_type,
        source_type=source_type,
        evidence_ids=evidence_ids or [],
        is_fact=is_fact,
        created_at=created_at,
        modified_at=created_at,
    )


async def _write_many(store: MemoryStore, records: list[MemoryRecord]) -> None:
    for r in records:
        await store.write(r)


class _RecordingStore(MemoryStore):
    """Wraps another MemoryStore and records every ``query()`` call's
    ``limit`` argument and the number of records returned.

    Used by ``test_candidate_limit_bounds_scan`` to DIRECTLY assert that
    a low ``candidate_limit`` is forwarded all the way to the store —
    i.e. the limit is enforced at the store boundary, not silently
    dropped or overridden by the retriever.
    """

    def __init__(self, inner: MemoryStore) -> None:
        self._inner = inner
        self.query_calls: list[dict[str, Any]] = []

    async def write(self, record: MemoryRecord) -> str:
        return await self._inner.write(record)

    async def read(self, record_id: str) -> MemoryRecord | None:
        return await self._inner.read(record_id)

    async def query(
        self,
        *,
        agent_id: str,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        memory_type: MemoryType | None = None,
        execution_id: str | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        records = await self._inner.query(
            agent_id=agent_id,
            domain=domain,
            user_id=user_id,
            context_id=context_id,
            memory_type=memory_type,
            execution_id=execution_id,
            limit=limit,
        )
        self.query_calls.append(
            {"limit": limit, "returned": len(records)}
        )
        return records

    async def delete(
        self,
        record_id: str,
        *,
        by: str | None = None,
        reason: str | None = None,
    ) -> bool:
        return await self._inner.delete(record_id, by=by, reason=reason)

    async def count(self, *, agent_id: str, domain: str = "default") -> int:
        return await self._inner.count(agent_id=agent_id, domain=domain)


# --------------------------------------------------------------------------- #
# 1. The 1499+1 regression: default config MUST retrieve the OLD relevant
#    memory (pre-fix, candidate_limit=1000, this FAILED — the OLD memory
#    was excluded from the candidate pool before ranking).
# --------------------------------------------------------------------------- #


async def test_relevant_old_memory_not_excluded_by_candidate_limit() -> None:
    """Write 1499 recent irrelevant EPISODIC memories + 1 OLD relevant
    FACTUAL memory (30 days old, oldest in the store). Call
    ``retrieve()`` with the DEFAULT ``RetrievalConfig`` (no overrides).
    Assert the OLD relevant memory IS in the top-5 results.

    Pre-fix (candidate_limit=1000): the store's
    ``ORDER BY created_at DESC LIMIT 1000`` returned only the 1000
    NEWEST records. The 30-day-old relevant memory (oldest in the
    store) was excluded from the candidate pool BEFORE ranking, so it
    could NEVER be retrieved no matter how well it matched the query.

    Post-fix (candidate_limit=10000): the candidate pool covers all
    1500 records, ranking surfaces the high-confidence lexical-match
    OLD memory into the top-5.

    This test uses the DEFAULT config — it does NOT artificially raise
    ``candidate_limit``. If this test fails, the default has regressed
    below 1500 (or the store stopped honouring the limit at the
    boundary).
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    # DEFAULT config — no candidate_limit override. This is the whole
    # point of the test: the default must be large enough to cover a
    # 1500-record store where the relevant record is the OLDEST.
    retriever = MemoryRetriever(mgr)
    assert retriever._config.candidate_limit >= 1500, (
        "default candidate_limit must be >= 1500 for this regression "
        "test to be meaningful — see RetrievalConfig docstring."
    )

    base = utc_now()
    # 1499 recent irrelevant EPISODIC memories — content has no overlap
    # with the query, confidence 0.3 (low).
    noise = [
        _make_record(
            record_id=f"noise-{i}",
            memory_type=MemoryType.EPISODIC,
            content=f"irrelevant topic {i}",
            confidence=0.3,
            created_at=base + timedelta(seconds=i),
        )
        for i in range(1499)
    ]
    # 1 OLD relevant FACTUAL memory — 30 days old, high confidence,
    # lexical match with the query.
    old_relevant = _make_record(
        record_id="old-factual",
        memory_type=MemoryType.FACTUAL,
        content="user prefers Python",
        confidence=0.95,
        created_at=base - timedelta(days=30),
        source_type="tool",
        evidence_ids=["ev-1"],
        is_fact=True,
    )
    await _write_many(store, noise + [old_relevant])

    results = await retriever.retrieve(
        agent_id="agent-A",
        user_id="alice",
        query="what language does user prefer",
    )
    retrieved_ids = {rm.record.record_id for rm in results}

    # Top-K default is 5 — confirm we got a non-empty result set.
    assert len(results) > 0, "retriever returned no results at all"
    assert len(results) <= 5, (
        f"retriever returned more than top_k=5 results: {len(results)}"
    )
    # The OLD relevant memory MUST be in the results — this is the
    # core assertion of the 1499+1 regression.
    assert "old-factual" in retrieved_ids, (
        "DEFAULT candidate_limit FAILED to retrieve the OLD relevant "
        "memory — the candidate pool excluded it before ranking. "
        f"retrieved_ids={retrieved_ids}"
    )


# --------------------------------------------------------------------------- #
# 2. The default-size assertion: candidate_limit is documented and large
#    enough to cover the 1499+1 scenario by default.
# --------------------------------------------------------------------------- #


async def test_candidate_limit_documented_contract() -> None:
    """Verify the ``candidate_limit`` contract:

      a. The field exists on ``RetrievalConfig``.
      b. The default is a documented, positive integer.
      c. The default is >= 1500 — i.e. the 1499+1 scenario from
         ``test_relevant_old_memory_not_excluded_by_candidate_limit``
         is covered OUT OF THE BOX, without any caller override.
      d. The default is bounded (not ``None`` / not infinity) — i.e.
         the scan-cost bound is real, not a placebo. This pins the
         Option-A choice (bounded-but-generous) over Option-B (no
         limit at all).
    """
    # (a) Field exists.
    assert hasattr(RetrievalConfig, "model_fields"), (
        "RetrievalConfig is a pydantic BaseModel — model_fields must exist"
    )
    assert "candidate_limit" in RetrievalConfig.model_fields, (
        "RetrievalConfig must declare a 'candidate_limit' field — "
        "the retriever depends on it as the default candidate pool size."
    )

    cfg = RetrievalConfig()
    # (b) Documented default + positive integer.
    assert isinstance(cfg.candidate_limit, int), (
        f"candidate_limit must be an int, got {type(cfg.candidate_limit)}"
    )
    assert cfg.candidate_limit > 0, (
        f"candidate_limit must be positive, got {cfg.candidate_limit}"
    )
    # (c) Default >= 1500 — the 1499+1 regression is covered by default.
    assert cfg.candidate_limit >= 1500, (
        f"candidate_limit default ({cfg.candidate_limit}) is below 1500 — "
        "the 1499+1 regression test would silently fail on default config. "
        "Either raise the default or mark this contract as broken."
    )
    # (d) Bounded — the scan-cost bound is real, not a placebo.
    #     (Option A: bounded-but-generous. NOT Option B: unbounded.)
    assert cfg.candidate_limit < 10**9, (
        f"candidate_limit default ({cfg.candidate_limit}) looks unbounded — "
        "Option-A contract requires a real (bounded) scan limit. If you "
        "intentionally switched to Option-B (no limit), update this test "
        "and the RetrievalConfig docstring to reflect the new contract."
    )

    # End-to-end: with DEFAULT config, a 1500-record store where the
    # relevant record is the OLDEST still surfaces it via the scorer.
    # (This is the same shape as test 1 but uses a smaller store size
    # to keep the contract test fast — the point is the default covers
    # it, not the exact 1499+1 number.)
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    retriever = MemoryRetriever(mgr)  # default config

    base = utc_now()
    noise = [
        _make_record(
            record_id=f"n-{i}",
            memory_type=MemoryType.EPISODIC,
            content=f"irrelevant topic {i}",
            confidence=0.3,
            created_at=base + timedelta(seconds=i),
        )
        for i in range(1499)
    ]
    old_relevant = _make_record(
        record_id="old-factual",
        memory_type=MemoryType.FACTUAL,
        content="user prefers Python",
        confidence=0.95,
        created_at=base - timedelta(days=30),
        source_type="tool",
        evidence_ids=["ev-1"],
        is_fact=True,
    )
    await _write_many(store, noise + [old_relevant])

    results = await retriever.retrieve(
        agent_id="agent-A",
        user_id="alice",
        query="what language does user prefer",
    )
    retrieved_ids = {rm.record.record_id for rm in results}
    assert "old-factual" in retrieved_ids, (
        "DEFAULT config (no overrides) failed to surface the OLD relevant "
        "memory — the documented contract (default >= 1500) is violated."
    )


# --------------------------------------------------------------------------- #
# 3. The bounded-scan assertion: candidate_limit DOES truncate the
#    candidate pool (the limit is enforced, not a no-op). This is the
#    NEGATIVE test from the task spec: with candidate_limit BELOW the
#    position of the relevant memory in the store's sort order, the
#    relevant memory IS excluded — documenting the KNOWN LIMITATION as
#    an opt-in via a low explicit limit.
# --------------------------------------------------------------------------- #


async def test_candidate_limit_bounds_scan() -> None:
    """Verify that with ``candidate_limit=10``, only 10 candidates are
    fetched from the store (the limit is enforced at the store
    boundary, not silently dropped or overridden by the retriever).

    We use a ``_RecordingStore`` wrapper that records every ``query()``
    call's ``limit`` argument and the number of records returned. This
    DIRECTLY tests the contract: a low ``candidate_limit`` is forwarded
    all the way to ``store.query(limit=10)``.

    We then ALSO verify the known limitation: with 50 records all
    matching the query and ``candidate_limit=10``, only the 10 NEWEST
    (by ``created_at DESC``) are ranked — the 40 OLDER records (which
    would otherwise match) are excluded from the candidate pool BEFORE
    ranking. This documents Option-A's trade-off: the limit is a real
    bound, and lowering it is a documented opt-in to the OLD-memory
    exclusion limitation.
    """
    inner = InMemoryMemoryStore()
    store = _RecordingStore(inner)
    mgr = MemoryManager(store=store)
    # top_k=50 so top_k does NOT clip below candidate_limit — the
    # candidate_limit is the only thing bounding the ranked pool.
    cfg = RetrievalConfig(candidate_limit=10, top_k=50, min_score=0.0)
    retriever = MemoryRetriever(mgr, config=cfg)

    base = utc_now()
    # 50 records — ALL match the query ("python testing") so all would
    # otherwise survive ranking. Only the candidate_limit keeps the
    # ranked pool at 10.
    records = [
        _make_record(
            record_id=f"rec-{i}",
            memory_type=MemoryType.SEMANTIC,
            content="python testing framework shared topic",
            confidence=0.5,
            created_at=base + timedelta(seconds=i),
        )
        for i in range(50)
    ]
    await _write_many(store, records)

    results = await retriever.retrieve(
        agent_id="agent-A",
        user_id="alice",
        query="python testing",
    )

    # The store was called exactly once with limit=10.
    assert len(store.query_calls) == 1, (
        f"expected exactly one store.query() call, got "
        f"{len(store.query_calls)}"
    )
    assert store.query_calls[0]["limit"] == 10, (
        f"store.query() was called with limit="
        f"{store.query_calls[0]['limit']}, expected 10 — the "
        "candidate_limit was not forwarded to the store boundary."
    )
    # The store returned exactly 10 records (slice [:10] of 50 matches).
    assert store.query_calls[0]["returned"] == 10, (
        f"store.query() returned {store.query_calls[0]['returned']} "
        "records, expected 10 — the InMemory store did not honour "
        "limit=10."
    )
    # The retriever ranked exactly 10 records (top_k=50 does not clip
    # below the 10-candidate pool; min_score=0.0 keeps all).
    assert len(results) == 10, (
        f"retriever returned {len(results)} results, expected 10 — "
        "the candidate_limit bound was not reflected in the ranked "
        "result set."
    )
    # The 10 ranked records are the 10 NEWEST (rec-40..rec-49), since
    # the store sorts by created_at DESC. This documents the known
    # limitation: the 40 OLDER matching records were excluded from
    # the candidate pool BEFORE ranking — they never had a chance to
    # be scored. Lowering candidate_limit is an opt-in to this
    # trade-off.
    ranked_ids = {rm.record.record_id for rm in results}
    expected_newest_10 = {f"rec-{i}" for i in range(40, 50)}
    assert ranked_ids == expected_newest_10, (
        f"ranked record set {ranked_ids} != expected newest 10 "
        f"{expected_newest_10} — the store did not return the 10 "
        "newest records by created_at DESC, OR the candidate_limit "
        "was not applied at the store boundary."
    )
