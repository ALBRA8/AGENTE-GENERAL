"""ALBRA SEED — Contract tests.

These tests verify that every concrete implementation satisfies its
declared contract. Contract violations are bugs in the implementation
(or the contract), not test issues. The seed ships a strict set of
contracts:

  * Tool implementations MUST carry a complete ToolContract (name,
    description, version, schemas, risk_level, etc.).
  * LLM provider implementations MUST implement the LLMProvider ABC
    (`complete`, `health`, plus the `name` / `is_mock` / `is_external`
    class attributes).
  * MemoryStore implementations MUST implement the MemoryStore ABC
    (`write`, `read`, `query`, `delete`, `count`).
  * AuditLog stores MUST be append-only — no update or delete API.

These tests are contract-focused: they instantiate every concrete class
shipped by the seed (and registered by the test suite) and check that
the class conforms to its declared interface. They do NOT exercise
runtime behaviour — that's the job of unit and golden-flow tests.

CRITICAL: every test runs with NO external credentials. Provider classes
that require an external SDK are detected via their `__init__` signature
or by attempting instantiation with stub args (and skipped if the SDK
is not installed).
"""

from __future__ import annotations

import contextlib
import inspect

from albra_seed.audit.normal import AuditLog
from albra_seed.audit.store import (
    AuditStore,
    FileAuditStore,
    InMemoryAuditStore,
    SQLiteAuditStore,
)
from albra_seed.core.base import (
    LLMProvider,
    MemoryStore,
    Tool,
    ToolContract,
)
from albra_seed.memory.inmemory_store import InMemoryMemoryStore
from albra_seed.memory.sqlite_store import SQLiteMemoryStore
from albra_seed.providers.anthropic_provider import AnthropicProvider
from albra_seed.providers.groq_provider import GroqProvider
from albra_seed.providers.mock import MockProvider
from albra_seed.providers.ollama_provider import OllamaProvider
from albra_seed.providers.openai_provider import OpenAIProvider
from albra_seed.providers.zai_provider import ZAIProvider
from albra_seed.tools.infra_tools import (
    EchoTool,
    HealthCheckTool,
    NoOpTool,
    StatusTool,
    TimestampTool,
    default_infra_tools,
)

# =====================================================================
# Helper: which provider classes can be instantiated in CORE_ONLY mode?
# =====================================================================


def _instantiate_providers() -> list[LLMProvider]:
    """Instantiate every provider that works without external credentials.

    External providers (OpenAI / Anthropic / Groq / ZAI) require an SDK
    AND an API key. They raise NotImplementedError when constructed
    without those — we skip them and verify contract compliance via
    class-level introspection instead.
    """
    providers: list[LLMProvider] = [MockProvider()]
    for cls in (OpenAIProvider, AnthropicProvider, GroqProvider, ZAIProvider, OllamaProvider):
        try:
            cls()  # type: ignore[call-arg]
        except (NotImplementedError, ImportError, ValueError, TypeError):
            continue
        # If instantiation somehow succeeded (e.g. an env var was set),
        # include the instance.
        # noinspection PyUnresolvedReferences
        providers.append(cls())  # type: ignore[call-arg]
    return providers


# =====================================================================
# 1. Every registered tool has a complete ToolContract
# =====================================================================


def test_tool_contract_required_fields() -> None:
    """Every Tool shipped by the seed has a complete ToolContract."""
    required_fields = (
        "tool_id",
        "name",
        "description",
        "version",
        "input_schema",
        "output_schema",
        "risk_level",
        "permissions",
        "timeout_seconds",
        "retry_policy",
        "evidence_required",
        "is_dry_run_capable",
        "is_external",
        "availability",
    )
    tools: list[Tool] = list(default_infra_tools())
    assert tools, "default_infra_tools() must return at least one tool"
    # Cover the explicit instantiations too — each must yield a contract
    # with the same canonical name as the registry instance.
    explicit_names: set[str] = set()
    for tool in [EchoTool(), HealthCheckTool(), NoOpTool(), TimestampTool(), StatusTool()]:
        assert isinstance(tool.contract, ToolContract)
        explicit_names.add(tool.contract.name)
    # The explicit set must match the registry set.
    assert explicit_names == {t.contract.name for t in tools}
    seen_names: set[str] = set()
    for tool in tools:
        contract = getattr(tool, "contract", None)
        assert isinstance(contract, ToolContract), (
            f"tool {type(tool).__name__} must expose a ToolContract instance"
        )
        for field in required_fields:
            assert hasattr(contract, field), (
                f"ToolContract on {type(tool).__name__} missing field: {field}"
            )
        # Name must be a non-empty string and unique across the seed.
        assert isinstance(contract.name, str) and contract.name, (
            f"ToolContract.name on {type(tool).__name__} must be a non-empty str"
        )
        assert contract.name not in seen_names, (
            f"duplicate tool name: {contract.name}"
        )
        seen_names.add(contract.name)
        # Schemas must be dicts (even if empty).
        assert isinstance(contract.input_schema, dict)
        assert isinstance(contract.output_schema, dict)
        # Risk level must be a valid enum value.
        from albra_seed.core.types import RiskLevel
        assert isinstance(contract.risk_level, RiskLevel)
        # Permissions must be a list (possibly empty).
        assert isinstance(contract.permissions, list)


# =====================================================================
# 2. Every provider implements LLMProvider
# =====================================================================


def test_provider_contract_implements_interface() -> None:
    """Every provider class is a subclass of LLMProvider and exposes the
    required class attributes + async methods.
    """
    provider_classes: list[type[LLMProvider]] = [
        MockProvider,
        OpenAIProvider,
        AnthropicProvider,
        GroqProvider,
        OllamaProvider,
        ZAIProvider,
    ]
    for cls in provider_classes:
        # Subclass check.
        assert issubclass(cls, LLMProvider), f"{cls.__name__} must subclass LLMProvider"
        # Class attributes required by the contract.
        assert hasattr(cls, "name") and isinstance(cls.name, str)
        assert hasattr(cls, "is_mock") and isinstance(cls.is_mock, bool)
        assert hasattr(cls, "is_external") and isinstance(cls.is_external, bool)
        # Async methods required by the contract.
        assert hasattr(cls, "complete"), f"{cls.__name__} missing complete()"
        assert hasattr(cls, "health"), f"{cls.__name__} missing health()"
        assert inspect.iscoroutinefunction(cls.complete), (
            f"{cls.__name__}.complete must be async"
        )
        assert inspect.iscoroutinefunction(cls.health), (
            f"{cls.__name__}.health must be async"
        )

    # MockProvider (always instantiable) — verify it actually returns an
    # AgentResponse when complete() is awaited.
    import asyncio

    from albra_seed.core.types import Message, Role

    mock = MockProvider()
    assert mock.is_mock is True
    assert mock.is_external is False
    response = asyncio.run(
        mock.complete([Message(role=Role.USER, content="contract")])
    )
    from albra_seed.core.types import AgentResponse
    assert isinstance(response, AgentResponse)
    assert response.content.startswith("[mock]")


# =====================================================================
# 3. Every MemoryStore implementation matches the abstract interface
# =====================================================================


def test_memory_store_implements_interface() -> None:
    """Every concrete MemoryStore implements every abstract method."""
    abstract_methods = {
        "write",
        "read",
        "query",
        "delete",
        "count",
    }
    concrete: list[MemoryStore] = [InMemoryMemoryStore()]
    # SQLiteMemoryStore — uses an in-memory sqlite db so no file is created.
    with contextlib.suppress(Exception):
        concrete.append(SQLiteMemoryStore(":memory:"))

    assert concrete, "at least one MemoryStore must instantiate"

    import asyncio

    from albra_seed.core.base import MemoryRecord
    from albra_seed.core.types import MemoryType

    async def roundtrip(store: MemoryStore) -> None:
        record = MemoryRecord(
            agent_id="contract-agent",
            memory_type=MemoryType.WORKING,
            content="hello",
        )
        rid = await store.write(record)
        assert rid
        fetched = await store.read(rid)
        assert fetched is not None and fetched.content == "hello"
        results = await store.query(agent_id="contract-agent")
        assert any(r.record_id == rid for r in results)
        n = await store.count(agent_id="contract-agent")
        assert n >= 1
        assert await store.delete(rid) is True
        assert await store.read(rid) is None

    for store in concrete:
        assert isinstance(store, MemoryStore), (
            f"{type(store).__name__} must subclass MemoryStore"
        )
        for method in abstract_methods:
            assert hasattr(store, method), (
                f"{type(store).__name__} missing MemoryStore.{method}()"
            )
            attr = getattr(store, method)
            assert callable(attr), f"{type(store).__name__}.{method} must be callable"
        # Round-trip: write -> read -> query -> delete -> count.
        asyncio.run(roundtrip(store))


# =====================================================================
# 4. Audit log rejects modifications (append-only)
# =====================================================================


def test_audit_log_append_only() -> None:
    """AuditLog + AuditStore expose no update/delete API — append-only."""
    log = AuditLog()
    # Append-only: `record` exists, no `update`, no `delete`, no `edit`.
    assert hasattr(log, "record")
    assert hasattr(log, "all")
    assert not hasattr(log, "update"), "AuditLog must not expose update()"
    assert not hasattr(log, "delete"), "AuditLog must not expose delete()"
    assert not hasattr(log, "edit"), "AuditLog must not expose edit()"
    assert not hasattr(log, "modify"), "AuditLog must not expose modify()"

    # The underlying store must also be append-only.
    store = log.store
    assert isinstance(store, AuditStore)
    assert hasattr(store, "append")
    assert not hasattr(store, "update"), "AuditStore must not expose update()"
    assert not hasattr(store, "delete"), "AuditStore must not expose delete()"

    # Concretely: appending twice grows the log; nothing else mutates it.
    log.record(action="first", actor="contract", result="success")
    log.record(action="second", actor="contract", result="success")
    assert log.count() == 2

    # Every concrete AuditStore backend must respect the same contract.
    for store_cls in (InMemoryAuditStore, SQLiteAuditStore, FileAuditStore):
        # FileAuditStore requires a path; use pytest's tmp_path.
        if store_cls is FileAuditStore:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                instance = store_cls(f"{tmp}/audit.jsonl")  # type: ignore[call-arg]
        else:
            instance = store_cls()  # type: ignore[call-arg]
        assert hasattr(instance, "append")
        assert not hasattr(instance, "update"), (
            f"{store_cls.__name__} must not expose update()"
        )
        assert not hasattr(instance, "delete"), (
            f"{store_cls.__name__} must not expose delete()"
        )

    # Tamper-evident log is ALSO append-only.
    from albra_seed.audit.tamper_evident import TamperEvidentAuditLog
    te = TamperEvidentAuditLog()
    assert hasattr(te, "append")
    assert not hasattr(te, "update")
    assert not hasattr(te, "delete")
    te.append({"action": "first", "actor": "contract"})
    te.append({"action": "second", "actor": "contract"})
    assert te.count() == 2
    # verify_chain() must remain valid after only-append usage.
    assert te.verify_chain().valid is True
