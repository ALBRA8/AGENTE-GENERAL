"""Smoke tests for the supported local ASGI entrypoint."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # type: ignore[import-untyped]

from albra_seed.local_api import app, runtime
from albra_seed.runtime.lifecycle import LifecyclePhase


def test_local_api_lifespan_and_health_endpoints() -> None:
    """The local entrypoint starts the runtime and serves health/readiness."""
    with TestClient(app) as client:
        assert runtime.lifecycle.phase == LifecyclePhase.READY

        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert isinstance(health.json(), dict)

        readiness = client.get("/api/v1/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["ready"] is True

    assert runtime.lifecycle.phase == LifecyclePhase.STOPPED
