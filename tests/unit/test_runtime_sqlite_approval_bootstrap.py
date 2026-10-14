"""Runtime integration tests for durable SQLite approvals."""
from __future__ import annotations

import pytest

from albra_seed.approvals import ApprovalEngine, SQLiteApprovalStore
from albra_seed.core.config import SeedConfig
from albra_seed.runtime.runtime import COMPONENT_APPROVAL_ENGINE, Runtime


@pytest.mark.asyncio
async def test_runtime_bootstraps_sqlite_approval_engine_from_config(tmp_path):
    db_path = tmp_path / "approvals.sqlite3"
    runtime = Runtime(config=SeedConfig(approval_db_path=str(db_path)))

    await runtime.startup()
    try:
        engine = runtime.get_component(COMPONENT_APPROVAL_ENGINE)
        assert isinstance(engine, ApprovalEngine)
        assert isinstance(engine.store, SQLiteApprovalStore)
        assert db_path.exists()
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_runtime_preserves_explicit_approval_engine(tmp_path):
    custom_engine = ApprovalEngine()
    runtime = Runtime(
        config=SeedConfig(approval_db_path=str(tmp_path / "approvals.sqlite3"))
    )
    runtime.register(COMPONENT_APPROVAL_ENGINE, custom_engine)

    await runtime.startup()
    try:
        assert runtime.get_component(COMPONENT_APPROVAL_ENGINE) is custom_engine
        assert custom_engine.store is None
    finally:
        await runtime.shutdown()
