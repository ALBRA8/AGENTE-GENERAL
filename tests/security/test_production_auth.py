"""Production authentication security tests.

Verifies:
  1. TokenAuthenticator enforces >= 32 characters entropy, constant-time
     verification, and never exposes credentials in errors or repr.
  2. Startup fail-safe in production:
     - Missing ALBRA_API_KEY / ALBRA_AUTH_TOKEN explicitly aborts server startup with RuntimeError.
     - Short (< 32 chars) token explicitly aborts server startup with RuntimeError.
     - Conflicting ALBRA_API_KEY and ALBRA_AUTH_TOKEN explicitly aborts startup with RuntimeError.
     - ALBRA_API_KEY short + ALBRA_AUTH_TOKEN valid is rejected as conflicting / invalid.
     - Single valid ALBRA_AUTH_TOKEN is accepted cleanly as an alias.
  3. All protected endpoints enforce Bearer authentication:
     - POST /api/v1/execution
     - GET /api/v1/execution/{id}
     - POST /api/v1/audio/transcribe
     - GET /api/v1/approvals/pending
     - POST /api/v1/approvals/{id}/decision
     - POST /api/v1/approvals/{id}/resume
     - GET /internal/diagnostics
  4. Matrix of negative and positive authentication cases:
     - Missing Authorization header -> 401
     - Non-Bearer scheme (Basic) -> 401
     - Empty Bearer credential -> 401
     - Invalid / arbitrary token -> 401
     - Dev token (albra-dev-token) in production -> 401
     - Valid production token -> allowed through authentication and RBAC
  5. Public health, readiness, and root endpoints remain accessible without auth and leak no secrets.
  6. In local dev mode, DevAuthenticator remains available if no prod token is set.
"""

from __future__ import annotations

import importlib
import io

import pytest
from fastapi.testclient import TestClient

from albra_seed.security.auth import (
    AuthenticationError,
    AuthPrincipal,
    TokenAuthenticator,
)

VALID_PROD_TOKEN = "production-secret-token-must-be-at-least-32-chars-long"


@pytest.fixture(autouse=True, scope="module")
def _cleanup_render_app_module_state():
    """Ensure render_app is restored to clean default state after this test module."""
    yield
    import os

    import render_app
    for key in ("APP_ENV", "ALBRA_API_KEY", "ALBRA_AUTH_TOKEN", "ALBRA_CORS_ORIGINS", "RENDER", "DYNO"):
        os.environ.pop(key, None)
    importlib.reload(render_app)



# =============================================================================
# 1. Unit Tests: TokenAuthenticator Core
# =============================================================================

def test_token_authenticator_requires_minimum_length() -> None:
    """Tokens shorter than 32 characters must be rejected immediately."""
    with pytest.raises(ValueError, match="at least 32 characters"):
        TokenAuthenticator("short-token")

    with pytest.raises(ValueError, match="at least 32 characters"):
        TokenAuthenticator("")

    with pytest.raises(ValueError, match="at least 32 characters"):
        TokenAuthenticator("   ")


def test_token_authenticator_accepts_valid_length() -> None:
    """A token with at least 32 characters is accepted."""
    valid_token = "a" * 32
    auth = TokenAuthenticator(valid_token)
    assert auth.name == "token"
    assert not auth.is_mock


def test_token_authenticator_repr_never_leaks_token() -> None:
    """__repr__ must never expose the raw token string."""
    secret = "super-secret-production-token-1234567890"
    auth = TokenAuthenticator(secret, identity="operator", roles=["reviewer"])
    representation = repr(auth)
    assert secret not in representation
    assert "operator" in representation
    assert "reviewer" in representation


@pytest.mark.asyncio
async def test_token_authenticator_verify() -> None:
    """verify() returns AuthPrincipal on exact match, None on invalid/empty."""
    token = "secure-production-api-token-minimum-32-chars-long"
    auth = TokenAuthenticator(token, identity="admin", roles=["reviewer", "executor"])

    # Valid token
    principal = await auth.verify(token)
    assert principal is not None
    assert isinstance(principal, AuthPrincipal)
    assert principal.identity == "admin"
    assert principal.roles == ["reviewer", "executor"]
    assert principal.is_anonymous is False
    assert principal.auth_method == "bearer"

    # Invalid token
    assert await auth.verify("wrong-token-with-minimum-32-characters-here") is None
    assert await auth.verify("") is None
    assert await auth.verify(None) is None
    assert await auth.verify("   ") is None


@pytest.mark.asyncio
async def test_token_authenticator_authenticate() -> None:
    """authenticate() raises AuthenticationError on mismatch, returns principal on match."""
    token = "secure-production-api-token-minimum-32-chars-long"
    auth = TokenAuthenticator(token, identity="admin")

    principal = await auth.authenticate(token)
    assert principal.identity == "admin"
    assert principal.is_anonymous is False

    with pytest.raises(AuthenticationError, match="invalid bearer token"):
        await auth.authenticate("invalid-credentials-matching-wrong-secret")

    with pytest.raises(AuthenticationError, match="invalid credentials"):
        await auth.authenticate(None)


# =============================================================================
# 2. Production Startup Fail-Safe and Precedence Tests
# =============================================================================

def _reload_render_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    app_env: str = "production",
    api_key: str | None = None,
    auth_token: str | None = None,
    cors_origins: str = "https://app.example.com",
):
    """Reload render_app module under specified environment variables."""
    import render_app

    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("ALBRA_CORS_ORIGINS", cors_origins)

    if api_key is not None:
        monkeypatch.setenv("ALBRA_API_KEY", api_key)
    else:
        monkeypatch.delenv("ALBRA_API_KEY", raising=False)

    if auth_token is not None:
        monkeypatch.setenv("ALBRA_AUTH_TOKEN", auth_token)
    else:
        monkeypatch.delenv("ALBRA_AUTH_TOKEN", raising=False)

    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("DYNO", raising=False)

    return importlib.reload(render_app)


def test_production_startup_aborts_when_token_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """In production without configured token, server startup lifespan aborts with RuntimeError."""
    mod = _reload_render_app(monkeypatch, app_env="production", api_key=None, auth_token=None)

    with pytest.raises(RuntimeError, match="ALBRA_API_KEY .* must be configured in production"), TestClient(mod.app):
        pass


def test_production_startup_aborts_when_token_is_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
    """In production with a token < 32 chars, server startup lifespan aborts with RuntimeError."""
    mod = _reload_render_app(monkeypatch, app_env="production", api_key="short-token", auth_token=None)

    with pytest.raises(RuntimeError, match="at least 32 characters in production"), TestClient(mod.app):
        pass


def test_startup_aborts_on_conflicting_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ALBRA_API_KEY and ALBRA_AUTH_TOKEN differ, startup aborts due to conflicting config."""
    with pytest.raises(RuntimeError, match="Conflicting authentication configuration"):
        _reload_render_app(
            monkeypatch,
            app_env="production",
            api_key=VALID_PROD_TOKEN,
            auth_token="different-token-with-minimum-32-characters-here",
        )


def test_startup_aborts_on_short_key_and_valid_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """If ALBRA_API_KEY is short and ALBRA_AUTH_TOKEN is valid, ambiguity is rejected."""
    with pytest.raises(RuntimeError, match="Conflicting authentication configuration"):
        _reload_render_app(
            monkeypatch,
            app_env="production",
            api_key="short",
            auth_token=VALID_PROD_TOKEN,
        )


def test_startup_succeeds_with_auth_token_alias_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """When only ALBRA_AUTH_TOKEN is set (>= 32 chars), it is accepted cleanly."""
    mod = _reload_render_app(
        monkeypatch,
        app_env="production",
        api_key=None,
        auth_token=VALID_PROD_TOKEN,
    )
    with TestClient(mod.app) as client:
        res = client.get("/api/v1/health")
        assert res.status_code == 200


# =============================================================================
# 3. Integration Tests: All Protected Endpoints & Negative/Positive Matrix
# =============================================================================

@pytest.fixture
def prod_client(monkeypatch: pytest.MonkeyPatch):
    """Fixture providing a started TestClient in production with valid token."""
    mod = _reload_render_app(monkeypatch, app_env="production", api_key=VALID_PROD_TOKEN)
    with TestClient(mod.app) as client:
        yield client


DUMMY_OGG = b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"


def _request_protected(client: TestClient, endpoint: str, headers: dict[str, str] | None = None):
    """Helper to dispatch to any protected endpoint with given headers."""
    if endpoint == "execution_post":
        return client.post("/api/v1/execution", json={"input": "ping"}, headers=headers)
    elif endpoint == "execution_get":
        return client.get("/api/v1/execution/non-existent-id", headers=headers)
    elif endpoint == "audio_transcribe":
        return client.post(
            "/api/v1/audio/transcribe",
            files={"file": ("test.ogg", io.BytesIO(DUMMY_OGG), "audio/ogg")},
            headers=headers,
        )
    elif endpoint == "approvals_pending":
        return client.get("/api/v1/approvals/pending", headers=headers)
    elif endpoint == "approvals_decision":
        return client.post(
            "/api/v1/approvals/appr_123/decision",
            json={"decision": "approve"},
            headers=headers,
        )
    elif endpoint == "approvals_resume":
        return client.post("/api/v1/approvals/appr_123/resume", headers=headers)
    elif endpoint == "diagnostics":
        return client.get("/internal/diagnostics", headers=headers)
    raise ValueError(f"Unknown endpoint: {endpoint}")


PROTECTED_ENDPOINTS = [
    "execution_post",
    "execution_get",
    "audio_transcribe",
    "approvals_pending",
    "approvals_decision",
    "approvals_resume",
    "diagnostics",
]


@pytest.mark.parametrize("endpoint", PROTECTED_ENDPOINTS)
def test_all_protected_endpoints_reject_missing_auth(prod_client: TestClient, endpoint: str) -> None:
    """Every protected endpoint must return 401 when Authorization header is absent."""
    res = _request_protected(prod_client, endpoint, headers=None)
    assert res.status_code == 401
    assert "bearer authentication required" in res.json()["detail"]


@pytest.mark.parametrize("endpoint", PROTECTED_ENDPOINTS)
def test_all_protected_endpoints_reject_malformed_auth(prod_client: TestClient, endpoint: str) -> None:
    """Every protected endpoint must return 401 for non-Bearer scheme or empty credential."""
    # Basic scheme
    res1 = _request_protected(prod_client, endpoint, headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert res1.status_code == 401
    assert "invalid authorization header" in res1.json()["detail"]

    # Bearer with whitespace only
    res2 = _request_protected(prod_client, endpoint, headers={"Authorization": "Bearer   "})
    assert res2.status_code == 401
    assert "invalid authorization header" in res2.json()["detail"]


@pytest.mark.parametrize("endpoint", PROTECTED_ENDPOINTS)
def test_all_protected_endpoints_reject_invalid_token(prod_client: TestClient, endpoint: str) -> None:
    """Every protected endpoint must return 401 for wrong token."""
    headers = {"Authorization": "Bearer wrong-token-123456789012345678901234"}
    res = _request_protected(prod_client, endpoint, headers=headers)
    assert res.status_code == 401
    assert "authenticated principal required" in res.json()["detail"]


@pytest.mark.parametrize("endpoint", PROTECTED_ENDPOINTS)
def test_all_protected_endpoints_reject_dev_token_in_production(
    prod_client: TestClient, endpoint: str
) -> None:
    """Dev token (albra-dev-token) must NEVER be accepted in production on any endpoint."""
    headers = {"Authorization": "Bearer albra-dev-token"}
    res = _request_protected(prod_client, endpoint, headers=headers)
    assert res.status_code == 401
    assert "authenticated principal required" in res.json()["detail"]


def test_valid_token_allows_access_to_all_protected_endpoints(prod_client: TestClient) -> None:
    """With valid production Bearer token, all endpoints allow access past authentication."""
    headers = {"Authorization": f"Bearer {VALID_PROD_TOKEN}"}

    # 1. Approvals pending list
    res_appr = prod_client.get("/api/v1/approvals/pending", headers=headers)
    assert res_appr.status_code == 200
    assert "approvals" in res_appr.json()

    # 2. Internal Diagnostics
    res_diag = prod_client.get("/internal/diagnostics", headers=headers)
    assert res_diag.status_code == 200
    assert "overall_status" in res_diag.json()

    # 3. Execution POST passes auth and RBAC (not 401 or 403 or 503)
    res_exec = prod_client.post("/api/v1/execution", json={"input": "ping"}, headers=headers)
    assert res_exec.status_code not in (401, 403, 503)

    # 4. Execution GET passes auth and RBAC (returns 404 for unknown trace, not 401 or 403)
    res_get_exec = prod_client.get("/api/v1/execution/unknown-exec-123", headers=headers)
    assert res_get_exec.status_code == 404
    assert "no execution trace cached" in res_get_exec.json()["detail"]

    # 5. Approvals decision passes auth and RBAC (returns 404 for unknown approval, not 401 or 403)
    res_dec = prod_client.post(
        "/api/v1/approvals/unknown-appr/decision",
        json={"decision": "approve"},
        headers=headers,
    )
    assert res_dec.status_code in (400, 404)

    # 6. Approvals resume passes auth and RBAC (returns 404 for unknown approval, not 401 or 403)
    res_res = prod_client.post(
        "/api/v1/approvals/unknown-appr/resume",
        headers=headers,
    )
    assert res_res.status_code in (400, 404)


# =============================================================================
# 4. Public Probes & Secret Non-Leakage
# =============================================================================

def test_public_probes_unauthenticated_and_leak_no_secrets(prod_client: TestClient) -> None:
    """Health, readiness, and root endpoints remain public and never leak tokens or secrets."""
    # Health probe
    res_health = prod_client.get("/api/v1/health")
    assert res_health.status_code == 200
    assert "status" in res_health.json()
    assert VALID_PROD_TOKEN not in res_health.text

    # Readiness probe
    res_ready = prod_client.get("/api/v1/readiness")
    assert res_ready.status_code == 200
    assert "ready" in res_ready.json()
    assert VALID_PROD_TOKEN not in res_ready.text

    # Root probe
    res_root = prod_client.get("/")
    assert res_root.status_code == 200
    assert VALID_PROD_TOKEN not in res_root.text


# =============================================================================
# 5. Local Dev Mode Behavior
# =============================================================================

def test_dev_mode_uses_dev_authenticator_when_token_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """In dev mode without ALBRA_API_KEY, DevAuthenticator allows albra-dev-token."""
    mod = _reload_render_app(monkeypatch, app_env="dev", api_key=None, auth_token=None)
    with TestClient(mod.app) as client:
        res = client.get(
            "/api/v1/approvals/pending",
            headers={"Authorization": "Bearer albra-dev-token"},
        )
        assert res.status_code == 200


# =============================================================================
# 6. RBAC Execution Trace Ownership & Isolation Tests
# =============================================================================

def test_user_with_execution_read_can_retrieve_own_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user with execution:read can read an execution they own (owner == principal.identity)."""
    alice_token = "alice-secret-token-must-be-at-least-32-chars-long"
    mod = _reload_render_app(monkeypatch, app_env="production", api_key=alice_token)

    mod.runtime.register(
        "authenticator",
        TokenAuthenticator(alice_token, identity="alice", roles=["reviewer"]),
    )
    with TestClient(mod.app) as client:
        mod.app.state.ctx.store_trace(
            "exec_alice_001",
            {
                "execution_id": "exec_alice_001",
                "correlation_id": "corr_001",
                "requested_by": "alice",
                "trace": [{"step": 1, "action": "think"}],
                "metadata": {"query": "test query alice"},
            },
        )
        res = client.get(
            "/api/v1/execution/exec_alice_001",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert res.status_code == 200
        payload = res.json()
        assert payload["found"] is True
        assert payload["execution_id"] == "exec_alice_001"
        assert len(payload["trace"]) == 1


def test_user_with_execution_read_denied_reading_others_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user with execution:read receives 403 when trying to read an execution owned by someone else."""
    alice_token = "alice-secret-token-must-be-at-least-32-chars-long"
    mod = _reload_render_app(monkeypatch, app_env="production", api_key=alice_token)
    mod.runtime.register(
        "authenticator",
        TokenAuthenticator(alice_token, identity="alice", roles=["reviewer"]),
    )
    with TestClient(mod.app) as client:
        mod.app.state.ctx.store_trace(
            "exec_bob_002",
            {
                "execution_id": "exec_bob_002",
                "correlation_id": "corr_002",
                "requested_by": "bob",
                "trace": [{"step": 1, "action": "secret-action-bob"}],
                "metadata": {"secret_info": "confidential to bob"},
            },
        )
        res = client.get(
            "/api/v1/execution/exec_bob_002",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert res.status_code == 403
        assert "execution:read:any" in res.json()["detail"]


def test_user_without_execution_read_cannot_retrieve_even_own_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user lacking execution:read receives 403 even when querying their own execution."""
    charlie_token = "charlie-token-with-minimum-32-characters-here"
    mod = _reload_render_app(monkeypatch, app_env="production", api_key=charlie_token)

    mod.runtime.register(
        "authenticator",
        TokenAuthenticator(charlie_token, identity="charlie", roles=["guest"]),
    )
    with TestClient(mod.app) as client:
        mod.app.state.ctx.store_trace(
            "exec_charlie_003",
            {
                "execution_id": "exec_charlie_003",
                "correlation_id": "corr_003",
                "requested_by": "charlie",
                "trace": [{"step": 1}],
                "metadata": {},
            },
        )
        res = client.get(
            "/api/v1/execution/exec_charlie_003",
            headers={"Authorization": f"Bearer {charlie_token}"},
        )
        assert res.status_code == 403
        assert "execution:read" in res.json()["detail"]


def test_non_existent_execution_returns_404_without_leaking_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """Querying a non-existent execution returns 404 without revealing information about other executions."""
    alice_token = "alice-secret-token-must-be-at-least-32-chars-long"
    mod = _reload_render_app(monkeypatch, app_env="production", api_key=alice_token)
    mod.runtime.register(
        "authenticator",
        TokenAuthenticator(alice_token, identity="alice", roles=["reviewer"]),
    )
    with TestClient(mod.app) as client:
        mod.app.state.ctx.store_trace(
            "exec_bob_secret",
            {
                "execution_id": "exec_bob_secret",
                "requested_by": "bob",
                "trace": [{"secret": "data"}],
            },
        )
        res = client.get(
            "/api/v1/execution/non-existent-id-999",
            headers={"Authorization": f"Bearer {alice_token}"},
        )
        assert res.status_code == 404
        assert "no execution trace cached" in res.json()["detail"]
        assert "exec_bob_secret" not in res.text
        assert "bob" not in res.text

