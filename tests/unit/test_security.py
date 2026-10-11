"""Unit tests for the security subpackage (F2-D).

Covers authenticator, RBAC roles/permissions, secrets managers
(never logging values), and the audit ACL.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from albra_seed.security import (
    ACLEntry,
    AccessControlList,
    AuthPrincipal,
    Authenticator,
    EnvSecretsManager,
    FileSecretsManager,
    NoopAuthenticator,
    Permission,
    PermissionRegistry,
    Role,
    RoleRegistry,
    SecretsManager,
)


def _run(coro):
    return asyncio.run(coro)


# =====================================================================
# AUTHENTICATOR
# =====================================================================


def test_noop_authenticator_works():
    """NoopAuthenticator returns an anonymous principal for any input."""
    auth = NoopAuthenticator()
    principal = _run(auth.authenticate(None))
    assert isinstance(principal, AuthPrincipal)
    assert principal.is_anonymous is True
    assert principal.kind == "anonymous"
    assert principal.identity == "anonymous"
    assert principal.auth_method == "noop"

    # verify() also returns an anonymous principal.
    verified = _run(auth.verify("any-token"))
    assert verified is not None
    assert verified.is_anonymous is True

    # NoopAuthenticator satisfies the ABC contract.
    assert isinstance(auth, Authenticator)
    assert auth.is_mock is True
    assert auth.name == "noop"


# =====================================================================
# ROLES + PERMISSIONS
# =====================================================================


def test_role_grant_revoke_permission():
    """PermissionRegistry.grant/revoke/has with DEFAULT-DENY semantics."""
    roles = RoleRegistry()
    analyst = Role(
        role_id="analyst",
        name="Data Analyst",
        description="Read-only data access",
    )
    roles.register(analyst)

    perms = PermissionRegistry(roles=roles)
    read_perm = Permission(
        id="tool.read.dataset",
        description="Read dataset",
    )
    perms.register(read_perm)

    # DEFAULT-DENY: analyst has nothing yet.
    assert perms.has("analyst", "tool.read.dataset") is False

    # Grant and verify.
    perms.grant("analyst", read_perm)
    assert perms.has("analyst", "tool.read.dataset") is True

    # Hierarchical: granting "tool.read" should imply "tool.read.dataset".
    admin_role = Role(role_id="admin", name="Admin")
    roles.register(admin_role)
    perms.register(Permission(id="tool.read", description="read anything"))
    perms.grant("admin", "tool.read")
    assert perms.has("admin", "tool.read.dataset") is True
    assert perms.has("admin", "tool.read.dataset.special") is True

    # Revoke and verify DEFAULT-DENY returns.
    assert perms.revoke("analyst", "tool.read.dataset") is True
    assert perms.has("analyst", "tool.read.dataset") is False

    # Revoke non-existent grant returns False.
    assert perms.revoke("analyst", "tool.read.dataset") is False

    # Cannot overwrite system role.
    with pytest.raises(ValueError):
        roles.register(Role(role_id="anonymous", name="x"))


# =====================================================================
# SECRETS — NEVER LOG VALUES
# =====================================================================


def test_secrets_manager_never_logs_values():
    """No log record produced by SecretsManager may contain secret values.

    Uses structlog's testing capture to collect event dicts emitted by
    the albra.security.secrets logger and asserts that the secret value
    NEVER appears in any captured event.
    """
    import structlog

    secret_value = "SUPER_SECRET_VALUE_12345_DO_NOT_LOG"

    with structlog.testing.capture_logs() as captured:
        mgr = EnvSecretsManager()
        _run(mgr.set("MY_SECRET_KEY", secret_value))
        got = _run(mgr.get("MY_SECRET_KEY"))
        assert got == secret_value
        _run(mgr.list_keys())
        _run(mgr.delete("MY_SECRET_KEY"))

    assert captured, "expected at least one structlog event from SecretsManager"

    # Inspect EVERY captured event — the secret value must NEVER appear.
    for event in captured:
        # event is a dict like {"event": "secrets.set", "key": "...", ...}
        # Serialize to a string and assert the value is absent.
        serialized = repr(event)
        assert secret_value not in serialized, (
            f"SECRET VALUE LEAKED INTO LOGS — event={event!r}"
        )
        # Sanity: 'value' field (if present) must be '<redacted>'.
        if "value" in event:
            assert event["value"] == "<redacted>", (
                f"value field not redacted: {event['value']!r}"
            )

    # __repr__ must not contain values either.
    assert secret_value not in repr(mgr)


def test_env_secrets_manager_reads_env():
    """EnvSecretsManager.get returns values from os.environ."""
    # Use a key unlikely to collide.
    key = "ALBRA_TEST_SECRET_XYZ_42"
    os.environ[key] = "env_value"
    try:
        mgr = EnvSecretsManager()
        assert _run(mgr.get(key)) == "env_value"
        assert key in _run(mgr.list_keys())

        # set() updates the env in-memory.
        _run(mgr.set("ALBRA_TEST_ANOTHER", "v2"))
        assert os.environ["ALBRA_TEST_ANOTHER"] == "v2"

        # delete() removes from env.
        assert _run(mgr.delete("ALBRA_TEST_ANOTHER")) is True
        assert "ALBRA_TEST_ANOTHER" not in os.environ
        assert _run(mgr.delete("ALBRA_TEST_ANOTHER")) is False
    finally:
        os.environ.pop(key, None)
        os.environ.pop("ALBRA_TEST_ANOTHER", None)

    # Prefix mode: only keys matching prefix are visible.
    os.environ["ALBRA_P_TEST_1"] = "a"
    os.environ["ALBRA_P_TEST_2"] = "b"
    os.environ["OTHER_KEY"] = "c"
    try:
        mgr = EnvSecretsManager(prefix="ALBRA_P_")
        keys = _run(mgr.list_keys())
        assert "TEST_1" in keys
        assert "TEST_2" in keys
        assert "OTHER_KEY" not in keys
        assert _run(mgr.get("TEST_1")) == "a"
    finally:
        for k in ("ALBRA_P_TEST_1", "ALBRA_P_TEST_2", "OTHER_KEY"):
            os.environ.pop(k, None)


def test_file_secrets_manager_reads_json():
    """FileSecretsManager reads + writes JSON secrets files."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "secrets.json"
        path.write_text(json.dumps({
            "API_KEY": "key-123",
            "API_SECRET": "secret-456",
        }), encoding="utf-8")

        mgr = FileSecretsManager(path)
        assert _run(mgr.get("API_KEY")) == "key-123"
        assert _run(mgr.get("API_SECRET")) == "secret-456"
        keys = _run(mgr.list_keys())
        assert "API_KEY" in keys
        assert "API_SECRET" in keys

        # set + delete persist to disk.
        _run(mgr.set("NEW_KEY", "new-val"))
        # Re-read from disk to verify persistence.
        mgr2 = FileSecretsManager(path)
        assert _run(mgr2.get("NEW_KEY")) == "new-val"
        assert _run(mgr.delete("API_KEY")) is True
        mgr3 = FileSecretsManager(path)
        assert _run(mgr3.get("API_KEY")) is None


# =====================================================================
# AUDIT ACL
# =====================================================================


def test_audit_acl_default_deny():
    """AccessControlList DEFAULT-DENY: nothing allowed without explicit grant."""
    acl = AccessControlList()
    # No grants — must DENY everything.
    assert acl.can("user:alice", "read", "audit:agent:albra-1") is False
    assert acl.can("user:alice", "export", "audit:agent:albra-1") is False

    # Grant explicit read on a pattern.
    acl.grant(ACLEntry(
        subject="user:alice",
        resource_pattern="audit:agent:*",
        action="read",
    ))
    assert acl.can("user:alice", "read", "audit:agent:albra-1") is True
    assert acl.can("user:alice", "read", "audit:agent:albra-2") is True

    # Other actions still denied for alice.
    assert acl.can("user:alice", "export", "audit:agent:albra-1") is False

    # Other users still denied.
    assert acl.can("user:bob", "read", "audit:agent:albra-1") is False

    # Wildcard subject + wildcard action.
    acl.grant(ACLEntry(
        subject="*",
        resource_pattern="audit:system:*",
        action="*",
    ))
    assert acl.can("user:anyone", "read", "audit:system:health") is True
    assert acl.can("user:anyone", "export", "audit:system:health") is True

    # Revoke and confirm default-deny returns.
    entries = acl.list_entries()
    assert len(entries) >= 1
    for e in entries:
        acl.revoke(e.entry_id)
    assert acl.can("user:alice", "read", "audit:agent:albra-1") is False
