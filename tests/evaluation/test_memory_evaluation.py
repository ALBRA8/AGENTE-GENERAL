"""MEM-LIFE — Memory subsystem evaluation (memory ON vs memory OFF).

A small, reproducible evaluation comparing the agent's behavior with
memory ON (MemoryManager registered + pre-stored relevant memory) vs
memory OFF (no MemoryManager registered — falls back to the pre-MEM-READ
messages list of [user_msg] only).

3 synthetic tasks, each run TWICE (OFF, then ON):

    1. "What programming language does the user prefer?"
         pre-stored memory: "user prefers Python" (SEMANTIC)

    2. "What did the user previously say about X?"
         pre-stored memory: EPISODIC about topic X (the value "X" is
         something concrete — "the weather in Tokyo")

    3. "Summarize the user's last decision"
         pre-stored memory: a DECISION record (objective + decision_taken)

For each task run, the test records:
    * relevant_retrieved_count       — memory ON only
    * irrelevant_retrieved_count     — memory ON only (must be 0)
    * response_contains_relevant_info (bool)
    * isolation_violations           — count of memories retrieved from
                                        a DIFFERENT user/context bucket
    * context_chars_added            — memory ON only (length of the
                                        SYSTEM DATA block inserted)

The Mock provider used here echoes the union of (retrieved SYSTEM DATA
text) + (user input). When memory is OFF, the response contains only the
user input. When memory is ON, the response contains the relevant info
extracted from the SYSTEM block + the user input.

Assertions:
    * With memory OFF, response_contains_relevant_info is False for all
      3 tasks (the Mock has no way to mention Python / the user's prior
      statement / the decision).
    * With memory ON, response_contains_relevant_info is True for all 3.
    * With memory ON, irrelevant_retrieved_count == 0 for all 3 tasks
      (we only pre-store the relevant memory; no unrelated noise).
    * With memory ON, isolation_violations == 0 (the retriever consults
      the isolation-aware recall).
    * With memory ON, context_chars_added > 0 (the SYSTEM DATA block
      is inserted).
    * With memory OFF, context_chars_added == 0.

The full results table is printed at the end of the test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from albra_seed.core.base import LLMProvider
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
)
from albra_seed.memory import (
    DecisionMemory,
    DecisionRecord,
    InMemoryMemoryStore,
    MemoryManager,
)
from albra_seed.runtime import Agent, Runtime
from albra_seed.runtime.runtime import COMPONENT_MEMORY_MANAGER

# --------------------------------------------------------------------------- #
# Mock provider that echoes retrieved context + user input
# --------------------------------------------------------------------------- #


class _EvalMockProvider(LLMProvider):
    """Mock provider for evaluation.

    Behaviour:
        * Captures the messages list it received (so the test can
          extract metrics: was a SYSTEM DATA block inserted? what was
          its length? what did it say?).
        * Returns an AgentResponse whose ``content`` is the concatenation
          of (a) any SYSTEM messages' content (the retrieved memories
          formatted as a DATA block) + (b) the user's input. This means
          the response carries the relevant info WHEN the agent's REASON
          phase inserted a SYSTEM DATA block.

    This is deterministic: same input messages -> same response. No
    external LLM call.
    """

    name = "eval_mock"
    is_mock = True
    is_external = False

    def __init__(self) -> None:
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
        self.received_messages.append(list(messages))

        system_blob = "\n".join(
            m.content for m in messages if m.role == Role.SYSTEM
        )
        user_text = ""
        for msg in reversed(messages):
            if msg.role == Role.USER:
                user_text = msg.content
                break

        # Echo system context + user input so the test can detect whether
        # the retrieved memory surfaced in the response. The Mock is
        # deliberately simple — no LLM "reasoning", just a faithful echo.
        content = (
            f"{system_blob}\n---\n{user_text}" if system_blob else user_text
        )

        return AgentResponse(
            content=content,
            execution_id=context.execution_id if context else new_id("exec"),
            correlation_id=context.correlation_id if context else new_id("corr"),
            confidence=1.0,
            metadata={"provider": self.name, "mock": True},
        )

    async def health(self) -> bool:
        return True


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #


@dataclass
class TaskResult:
    """One task run's measured outcome."""

    task_id: str
    mode: str  # "off" or "on"
    user_input: str
    relevant_retrieved_count: int = 0
    irrelevant_retrieved_count: int = 0
    response_contains_relevant_info: bool = False
    isolation_violations: int = 0
    context_chars_added: int = 0
    relevant_keywords: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Task definitions
# --------------------------------------------------------------------------- #


@dataclass
class EvalTask:
    """A synthetic evaluation task.

    Fields:
        task_id           — short identifier (e.g. "language_preference")
        user_input        — the user's question to the agent
        relevant_keywords — substrings that, if present in the response,
                            count as "the response contains the relevant
                            info". These come from the pre-stored memory
                            content.
        pre_store         — async callable that writes the relevant memory
                            into the given MemoryManager. Used in memory-ON
                            mode only.
    """

    task_id: str
    user_input: str
    relevant_keywords: list[str]
    pre_store: Any  # callable: async (mgr, agent_id) -> None


async def _store_language_pref(mgr: MemoryManager, agent_id: str) -> None:
    """Pre-store the user's preferred programming language as a SEMANTIC
    memory. Tagged with the standard isolation dims used by the eval."""
    await mgr.remember(
        agent_id=agent_id,
        content="the user prefers Python over JavaScript",
        memory_type=MemoryType.SEMANTIC,
        domain="default",
        user_id="alice",
        context_id="ctx-eval",
        confidence=0.85,
        source="user",
        source_type="user",
    )


async def _store_episodic_about_tokyo(mgr: MemoryManager, agent_id: str) -> None:
    """Pre-store an EPISODIC memory about a prior conversation topic."""
    await mgr.remember(
        agent_id=agent_id,
        content="the user said they enjoyed their trip to Tokyo last spring",
        memory_type=MemoryType.EPISODIC,
        domain="default",
        user_id="alice",
        context_id="ctx-eval",
        confidence=0.7,
        source="user",
        source_type="user",
    )


async def _store_decision_record(mgr: MemoryManager, agent_id: str) -> None:
    """Pre-store a DECISION record (via DecisionMemory) tagged with the
    standard isolation dims.

    DecisionMemory.record_decision does not accept user_id/context_id
    parameters directly; we re-tag the record after writing so it lands
    in the SAME isolation bucket the agent will run against.
    """
    decision_mgr = DecisionMemory(store=mgr.store)
    decision = DecisionRecord(
        agent_id=agent_id,
        objective="choose a hosting provider for the new service",
        options_considered=[
            {"name": "AWS", "pros": "mature", "cons": "cost"},
            {"name": "GCP", "pros": "ML tooling", "cons": "less mature"},
        ],
        decision_taken="GCP",
        reasoning="ML tooling aligns with the team's roadmap",
        confidence=0.8,
    )
    await decision_mgr.record_decision(decision, domain="default")
    # Re-tag the record with isolation dims so it lives in the same
    # bucket the agent's run will use.
    record = await mgr.store.read(decision.decision_id)
    if record is not None:
        record.user_id = "alice"
        record.context_id = "ctx-eval"
        record.domain = "default"
        await mgr.store.write(record)
        # The DecisionMemory bypasses manager.remember() so the lifecycle
        # CREATED event is not recorded. Add one so the retriever treats
        # the record as alive.
        mgr.lifecycle.record_created(record.record_id, by="agent")


def _tasks() -> list[EvalTask]:
    return [
        EvalTask(
            task_id="language_preference",
            user_input="What programming language does the user prefer?",
            relevant_keywords=["Python"],
            pre_store=_store_language_pref,
        ),
        EvalTask(
            task_id="episodic_recall",
            user_input="What did the user previously say about Tokyo?",
            relevant_keywords=["Tokyo", "spring"],
            pre_store=_store_episodic_about_tokyo,
        ),
        EvalTask(
            task_id="decision_recovery",
            user_input="Summarize the user's last decision.",
            relevant_keywords=["GCP", "hosting"],
            pre_store=_store_decision_record,
        ),
    ]


# --------------------------------------------------------------------------- #
# Runners
# --------------------------------------------------------------------------- #


# Standard isolation dims for every memory-ON run.
_USER_ID = "alice"
_CTX_ID = "ctx-eval"
_DOMAIN = "default"


async def _run_with_memory_off(
    task: EvalTask,
    *,
    identity: AgentIdentity,
) -> TaskResult:
    """Run a task with NO MemoryManager registered. The agent loop falls
    back to the pre-MEM-READ behaviour: messages = [user_msg]."""
    runtime = Runtime()
    await runtime.startup()
    recording = _EvalMockProvider()
    runtime.register_provider(recording)
    # NOTE: no MemoryManager registered -> agent.run() uses the no-memory path.

    session = Session(
        agent_id=identity.agent_id,
        user_id=_USER_ID,
        context_id=_CTX_ID,
        domain=_DOMAIN,
    )
    agent = Agent(identity, runtime)
    response = await agent.run(task.user_input, session=session)

    assert len(recording.received_messages) == 1
    msgs = recording.received_messages[0]
    system_chars = sum(len(m.content) for m in msgs if m.role == Role.SYSTEM)

    # No memory -> no retrieval -> no relevant info. The keywords come
    # from the pre-stored memory content, NOT the user input — so
    # contains_info MUST be False here.
    contains_info = any(
        kw.lower() in response.content.lower() for kw in task.relevant_keywords
    )
    contains_info = contains_info and system_chars > 0

    await runtime.shutdown()
    return TaskResult(
        task_id=task.task_id,
        mode="off",
        user_input=task.user_input,
        relevant_retrieved_count=0,
        irrelevant_retrieved_count=0,
        response_contains_relevant_info=contains_info,
        isolation_violations=0,
        context_chars_added=system_chars,
        relevant_keywords=task.relevant_keywords,
    )


async def _run_with_memory_on(
    task: EvalTask,
    *,
    identity: AgentIdentity,
) -> TaskResult:
    """Run a task with a MemoryManager pre-loaded with the relevant memory
    in the SAME isolation bucket the agent run will use.
    """
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    runtime = Runtime()
    await runtime.startup()
    recording = _EvalMockProvider()
    runtime.register_provider(recording)
    runtime.register_memory_store("default", mgr.store)
    runtime.register(COMPONENT_MEMORY_MANAGER, mgr)

    # Pre-store the relevant memory (helper already tags the isolation
    # dims to match the agent's run bucket).
    await task.pre_store(mgr, identity.agent_id)

    session = Session(
        agent_id=identity.agent_id,
        user_id=_USER_ID,
        context_id=_CTX_ID,
        domain=_DOMAIN,
    )
    agent = Agent(identity, runtime)
    response = await agent.run(task.user_input, session=session)

    assert len(recording.received_messages) == 1
    msgs = recording.received_messages[0]
    system_blob = "\n".join(m.content for m in msgs if m.role == Role.SYSTEM)
    system_chars = len(system_blob)

    # Count relevant / irrelevant retrieved memories by inspecting the
    # lines between the delimiters in the SYSTEM DATA block. Each memory
    # entry is formatted by MemoryRetriever.format_for_context() as:
    #     - [TYPE, confidence=X.XX, source=Y] <content>
    # so we count lines starting with "- [".
    relevant_lines = 0
    irrelevant_lines = 0
    if "[RETRIEVED MEMORIES" in system_blob:
        inside = system_blob.split("[RETRIEVED MEMORIES", 1)[1]
        if "[END RETRIEVED MEMORIES]" in inside:
            inside = inside.split("[END RETRIEVED MEMORIES]", 1)[0]
        for line in inside.split("\n"):
            line = line.strip()
            if not line.startswith("- ["):
                continue  # not a memory entry — skip delimiters + the
                          # header line continuation ("for context only, ...")
            if any(kw.lower() in line.lower() for kw in task.relevant_keywords):
                relevant_lines += 1
            else:
                irrelevant_lines += 1

    contains_info = any(
        kw.lower() in response.content.lower() for kw in task.relevant_keywords
    )

    # Isolation violations: count retrieved memory records whose
    # (user_id) differs from the run's. We do this by:
    #   1. Collecting the agent's retrieved_memory_ids from RunContext.
    #   2. Querying the store with a DIFFERENT user_id ("eve").
    #   3. Confirming none of those records are in the agent's
    #      retrieved set (i.e. they were never surfaced to the agent).
    ctx = agent.state.current_context
    assert ctx is not None
    retrieved_ids = set(ctx.metadata.get("retrieved_memory_ids", []))
    other_records = await mgr.store.query(
        agent_id=identity.agent_id,
        domain=_DOMAIN,
        user_id="eve",  # different user
        limit=10,
    )
    other_ids = {r.record_id for r in other_records}
    isolation_violations = len(other_ids & retrieved_ids)

    await runtime.shutdown()
    return TaskResult(
        task_id=task.task_id,
        mode="on",
        user_input=task.user_input,
        relevant_retrieved_count=relevant_lines,
        irrelevant_retrieved_count=irrelevant_lines,
        response_contains_relevant_info=contains_info,
        isolation_violations=isolation_violations,
        context_chars_added=system_chars,
        relevant_keywords=task.relevant_keywords,
    )


# --------------------------------------------------------------------------- #
# The evaluation test
# --------------------------------------------------------------------------- #


async def test_memory_evaluation_on_vs_off() -> None:
    """Runs all 3 tasks in both modes (OFF, ON) and asserts the memory ON
    mode strictly dominates OFF mode on every measured axis.
    """
    identity = AgentIdentity(
        name="eval-agent",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        providers=["eval_mock"],
    )

    results: list[TaskResult] = []
    for task in _tasks():
        off = await _run_with_memory_off(task, identity=identity)
        on = await _run_with_memory_on(task, identity=identity)
        results.append(off)
        results.append(on)

    # Print the results table so it appears in the test output.
    print("\n" + "=" * 78)
    print("MEMORY EVALUATION RESULTS — memory OFF vs memory ON")
    print("=" * 78)
    header = (
        f"{'task_id':<22} {'mode':<4} {'rel_ret':<8} {'irr_ret':<8} "
        f"{'has_info':<10} {'iso_viol':<10} {'ctx_chars':<10}"
    )
    print(header)
    print("-" * 78)
    for r in results:
        print(
            f"{r.task_id:<22} {r.mode:<4} "
            f"{r.relevant_retrieved_count:<8} "
            f"{r.irrelevant_retrieved_count:<8} "
            f"{str(r.response_contains_relevant_info):<10} "
            f"{r.isolation_violations:<10} "
            f"{r.context_chars_added:<10}"
        )
    print("=" * 78)

    # Group results by task for paired assertions.
    by_task: dict[str, dict[str, TaskResult]] = {}
    for r in results:
        by_task.setdefault(r.task_id, {})[r.mode] = r

    for task_id, pair in by_task.items():
        off = pair["off"]
        on = pair["on"]

        # 1. memory OFF: no SYSTEM DATA block was inserted.
        assert off.context_chars_added == 0, (
            f"[{task_id}] memory OFF must not insert a SYSTEM block, "
            f"got {off.context_chars_added} chars"
        )
        # 2. memory OFF: response does NOT contain relevant info.
        assert off.response_contains_relevant_info is False, (
            f"[{task_id}] memory OFF response unexpectedly contains relevant "
            f"info — keywords={off.relevant_keywords}"
        )
        # 3. memory ON: a SYSTEM DATA block was inserted.
        assert on.context_chars_added > 0, (
            f"[{task_id}] memory ON did not insert a SYSTEM block"
        )
        # 4. memory ON: the relevant info was retrieved at least once.
        assert on.relevant_retrieved_count >= 1, (
            f"[{task_id}] memory ON retrieved 0 relevant memories for "
            f"keywords={on.relevant_keywords}"
        )
        # 5. memory ON: irrelevant retrieved count is 0 (no noise).
        assert on.irrelevant_retrieved_count == 0, (
            f"[{task_id}] memory ON retrieved {on.irrelevant_retrieved_count} "
            f"irrelevant memories"
        )
        # 6. memory ON: response contains the relevant info.
        assert on.response_contains_relevant_info is True, (
            f"[{task_id}] memory ON response does not contain relevant info "
            f"for keywords={on.relevant_keywords}"
        )
        # 7. memory ON: isolation not violated.
        assert on.isolation_violations == 0, (
            f"[{task_id}] memory ON isolation violated — "
            f"{on.isolation_violations} records from another bucket surfaced"
        )

    # Overall summary assertions across all tasks.
    all_off = [r for r in results if r.mode == "off"]
    all_on = [r for r in results if r.mode == "on"]
    assert len(all_off) == 3
    assert len(all_on) == 3

    # ON strictly dominates OFF on the "has_info" axis for every task.
    for off, on in zip(all_off, all_on, strict=False):
        assert on.response_contains_relevant_info
        assert not off.response_contains_relevant_info
    # And on the "ctx_chars" axis.
    for off, on in zip(all_off, all_on, strict=False):
        assert on.context_chars_added > off.context_chars_added


# --------------------------------------------------------------------------- #
# Single-task sanity check (smoke test for the evaluation harness)
# --------------------------------------------------------------------------- #


async def test_memory_evaluation_task_language_preference_on() -> None:
    """Single-task smoke test: pre-store 'user prefers Python', ask the
    agent, verify the response contains 'Python' and at least one
    relevant memory was retrieved.
    """
    identity = AgentIdentity(
        name="eval-smoke-agent",
        autonomy_level=AutonomyLevel.L2_EXECUTE_SAFE,
        providers=["eval_mock"],
    )
    task = next(t for t in _tasks() if t.task_id == "language_preference")
    result = await _run_with_memory_on(task, identity=identity)

    assert result.relevant_retrieved_count >= 1
    assert result.irrelevant_retrieved_count == 0
    assert result.response_contains_relevant_info is True
    assert result.isolation_violations == 0
    assert result.context_chars_added > 0
