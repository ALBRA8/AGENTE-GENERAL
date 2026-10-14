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

def _create_interrupted_dispatch(db_path):
    from albra_seed.approvals.request import ApprovalRequest
    from albra_seed.core.types import ApprovalStatus

    store = SQLiteApprovalStore(db_path)
    request = store.create(ApprovalRequest(action="external.write"), call_id="call-crashed")
    assert store.transition(request.approval_id, ApprovalStatus.APPROVED, decided_by="reviewer")
    assert store.prepare_dispatch(request.approval_id, "call-crashed")
    assert store.claim_dispatch("call-crashed", "worker-before-crash")


@pytest.mark.asyncio
async def test_runtime_startup_recovers_interrupted_sqlite_dispatches(tmp_path):
    db_path = tmp_path / "approvals.sqlite3"
    _create_interrupted_dispatch(db_path)
    runtime = Runtime(config=SeedConfig(approval_db_path=str(db_path)))

    await runtime.startup()
    try:
        engine = runtime.get_component(COMPONENT_APPROVAL_ENGINE)
        dispatch = engine.store.dispatch("call-crashed")
        assert dispatch["state"] == "uncertain"
        assert "outcome unknown" in dispatch["error"]
        assert not engine.store.claim_dispatch("call-crashed", "worker-after-restart")
    finally:
        await runtime.shutdown()


@pytest.mark.asyncio
async def test_runtime_startup_recovers_dispatches_in_explicit_sqlite_engine(tmp_path):
    db_path = tmp_path / "approvals.sqlite3"
    _create_interrupted_dispatch(db_path)
    custom_engine = ApprovalEngine(store=SQLiteApprovalStore(db_path))
    runtime = Runtime(config=SeedConfig(approval_db_path=str(db_path)))
    runtime.register(COMPONENT_APPROVAL_ENGINE, custom_engine)

    await runtime.startup()
    try:
        assert runtime.get_component(COMPONENT_APPROVAL_ENGINE) is custom_engine
        dispatch = custom_engine.store.dispatch("call-crashed")
        assert dispatch["state"] == "uncertain"
        assert not custom_engine.store.claim_dispatch("call-crashed", "worker-after-restart")
    finally:
        await runtime.shutdown()

