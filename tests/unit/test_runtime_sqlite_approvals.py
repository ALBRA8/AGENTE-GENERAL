"""Runtime wiring tests for opt-in durable SQLite approvals."""

from __future__ import annotations


from albra_seed.approvals import ApprovalEngine, SQLiteApprovalStore
from albra_seed.core.config import SeedConfig
from albra_seed.runtime.runtime import COMPONENT_APPROVAL_ENGINE, Runtime


async def test_runtime_bootstraps_sqlite_approval_engine_when_configured(tmp_path) -> None:
    db_path = tmp_path / "approvals.sqlite3"
    runtime = Runtime(SeedConfig(approval_db_path=str(db_path)))

    await runtime.startup()
    engine = runtime.get_component(COMPONENT_APPROVAL_ENGINE)

    assert isinstance(engine, ApprovalEngine)
    assert isinstance(engine.store, SQLiteApprovalStore)
    assert db_path.exists()

    result = await engine.request("send_report")
    assert engine.store.get(result.approval_id) is not None
    await runtime.shutdown()

    restarted = Runtime(SeedConfig(approval_db_path=str(db_path)))
    await restarted.startup()
    restored = restarted.get_component(COMPONENT_APPROVAL_ENGINE)
    assert restored.store.get(result.approval_id) is not None
    await restarted.shutdown()


async def test_explicit_approval_engine_wins_over_sqlite_bootstrap(tmp_path) -> None:
    explicit = ApprovalEngine()
    runtime = Runtime(SeedConfig(approval_db_path=str(tmp_path / "unused.sqlite3")))
    runtime.register(COMPONENT_APPROVAL_ENGINE, explicit)

    await runtime.startup()

    assert runtime.get_component(COMPONENT_APPROVAL_ENGINE) is explicit
    assert not (tmp_path / "unused.sqlite3").exists()
    await runtime.shutdown()


async def test_sqlite_approval_engine_is_not_enabled_by_default() -> None:
    runtime = Runtime(SeedConfig())

    await runtime.startup()

    assert runtime.get_component(COMPONENT_APPROVAL_ENGINE) is None
    await runtime.shutdown()


def test_approval_db_path_loads_from_environment(monkeypatch, tmp_path) -> None:
    db_path = str(tmp_path / "approvals.sqlite3")
    monkeypatch.setenv("ALBRA_APPROVAL_DB_PATH", db_path)

    config = SeedConfig.from_env()

    assert config.approval_db_path == db_path
