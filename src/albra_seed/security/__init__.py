"""Security subpackage — authentication, RBAC, secrets, audit ACL.

The seed ships NO domain knowledge here:
  - NoopAuthenticator (CORE_ONLY default).
  - RoleRegistry with a built-in `anonymous` role (no permissions).
  - PermissionRegistry with DEFAULT-DENY `has()`.
  - EnvSecretsManager (default), FileSecretsManager, VaultSecretsManager
    (lazy hvac).
  - AccessControlList for audit-log access (DEFAULT-DENY).

CRITICAL RULES enforced in this subpackage:
  - DEFAULT-DENY everywhere: `has()` returns False unless explicitly granted;
    ACL `can()` returns False unless explicitly granted.
  - Secrets are NEVER logged (see `secrets._safe_log`).
  - Authentication ≠ Authorization. The Authenticator only answers WHO;
    the firewall + policy engine answer WHAT they may do.
"""

from albra_seed.security.audit_acl import AccessControlList, ACLEntry
from albra_seed.security.auth import (
    AuthenticationError,
    Authenticator,
    AuthPrincipal,
    NoopAuthenticator,
)
from albra_seed.security.identity_resolver import IdentityResolver
from albra_seed.security.permissions import Permission, PermissionRegistry
from albra_seed.security.roles import Role, RoleRegistry
from albra_seed.security.secrets import (
    EnvSecretsManager,
    FileSecretsManager,
    SecretsManager,
    VaultSecretsManager,
)

__all__ = [
    "Authenticator",
    "AuthPrincipal",
    "AuthenticationError",
    "NoopAuthenticator",
    "IdentityResolver",
    "Role",
    "RoleRegistry",
    "Permission",
    "PermissionRegistry",
    "SecretsManager",
    "EnvSecretsManager",
    "FileSecretsManager",
    "VaultSecretsManager",
    "AccessControlList",
    "ACLEntry",
]
