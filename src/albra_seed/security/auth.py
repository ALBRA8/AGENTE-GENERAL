"""Authenticator interface + NoopAuthenticator (CORE_ONLY default).

Future specialization layers may add JWT, OAuth, API key, mTLS, SAML, OIDC.
The seed ships only the NoopAuthenticator so CORE_ONLY mode always works.

Authentication answers: WHO is the actor?
Authorization (handled in `permissions.py`, `firewall/`, `policy/`) answers:
WHAT is that actor allowed to do?

These two concerns must never be conflated.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now


class AuthPrincipal(BaseModel):
    """An authenticated principal — the actor behind an action.

    `kind` is one of: agent | user | service | system | anonymous.
    `identity` is the stable identifier (agent_id, user_id, service_id, ...).
    `roles` are RBAC role ids the principal has been granted.
    `metadata` carries authentication-method-specific claims (JWT payload,
    OAuth scopes, etc.).
    """

    principal_id: str = Field(default_factory=lambda: new_id("prin"))
    kind: str = "anonymous"
    identity: str = "anonymous"
    roles: list[str] = Field(default_factory=list)
    authenticated_at: datetime = Field(default_factory=utc_now)
    auth_method: str = "noop"
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_anonymous: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Safe representation for logging — never includes secrets/tokens."""
        return {
            "principal_id": self.principal_id,
            "kind": self.kind,
            "identity": self.identity,
            "roles": list(self.roles),
            "auth_method": self.auth_method,
            "authenticated_at": self.authenticated_at.isoformat(),
            "is_anonymous": self.is_anonymous,
        }


class Authenticator(ABC):
    """Universal authenticator interface.

    Every specialization layer (JWT, OAuth, API key, mTLS, SAML) implements
    this interface. The runtime looks up authenticators by name via the
    component registry; the default authenticator is `NoopAuthenticator`.
    """

    name: str = "noop"
    is_mock: bool = False

    @abstractmethod
    async def authenticate(self, credentials: Any) -> AuthPrincipal:
        """Validate credentials and return an AuthPrincipal.

        Raises `AuthenticationError` on invalid/expired credentials.
        Implementations MUST NOT log credential values.
        """
        raise NotImplementedError

    @abstractmethod
    async def verify(self, token: str | None) -> AuthPrincipal | None:
        """Verify a bearer token and return the principal, or None.

        Returns None (never raises) on invalid tokens so callers can
        distinguish "unauthenticated" from "internal error".
        """
        raise NotImplementedError


class AuthenticationError(Exception):
    """Raised when authentication fails (invalid/expired credentials)."""


class NoopAuthenticator(Authenticator):
    """Default authenticator for CORE_ONLY mode.

    Accepts everything as anonymous. Every principal returned is anonymous
    with no roles. This is intentionally permissive in CORE_ONLY — the
    FIREWALL and POLICY ENGINE still DEFAULT-DENY every external action,
    so anonymity never implies capability.
    """

    name = "noop"
    is_mock = True

    async def authenticate(self, credentials: Any = None) -> AuthPrincipal:  # noqa: ARG002
        # Coerce anything into an anonymous principal.
        if isinstance(credentials, AuthPrincipal):
            return credentials
        return AuthPrincipal(
            kind="anonymous",
            identity="anonymous",
            auth_method="noop",
            is_anonymous=True,
        )

    async def verify(self, token: str | None = None) -> AuthPrincipal | None:  # noqa: ARG002
        # Noop authenticator treats every token as anonymous.
        return AuthPrincipal(
            kind="anonymous",
            identity="anonymous",
            auth_method="noop",
            is_anonymous=True,
        )


__all__ = [
    "AuthPrincipal",
    "Authenticator",
    "AuthenticationError",
    "NoopAuthenticator",
]
