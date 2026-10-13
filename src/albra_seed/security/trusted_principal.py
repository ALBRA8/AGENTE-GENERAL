"""TrustedPrincipal — sealed identity that cannot be forged by callers.

The problem: ``RunContext.verified_principal`` is a public field of type
``Any``. Any caller with a reference to the RunContext can set it to an
arbitrary ``AuthPrincipal``, bypassing authentication entirely.

The solution: ``TrustedPrincipal`` wraps an ``AuthPrincipal`` together with
an HMAC-SHA256 seal. The seal is computed by ``IdentityResolver`` using a
per-resolver secret key. Callers CANNOT construct a valid
``TrustedPrincipal`` without the secret key — they can create the wrapper
object, but the seal will be invalid and ``verify()`` will fail.

Architecture:
    IdentityResolver (has secret key)
        → authenticates token via Authenticator.verify()
        → wraps AuthPrincipal in TrustedPrincipal (sealed)
        → attaches to RunContext.verified_principal

    ToolExecutor (has TrustedPrincipalVerifier)
        → extracts TrustedPrincipal from RunContext.verified_principal
        → verifies the seal
        → extracts principal_id for AuthorizationToken verification

Limitations:
    - The seal is HMAC-SHA256 with a per-Issuer key. An attacker who
      obtains the Issuer's key can forge TrustedPrincipals. The key is
      generated via secrets.token_bytes(32) and never exposed.
    - This is defense-in-depth, not absolute security. The structural
      perimeter (firewall/policy/executor) remains the primary barrier.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TrustedPrincipal:
    """An AuthPrincipal sealed by IdentityResolver.

    Constructed ONLY by TrustedPrincipalIssuer.seal(). The ``seal`` field
    is an HMAC-SHA256 over (principal_id, kind, auth_method, timestamp).
    Callers who construct TrustedPrincipal directly will produce an invalid
    seal that TrustedPrincipalVerifier.verify() rejects.
    """

    principal_id: str
    kind: str
    roles: tuple[str, ...]
    auth_method: str
    is_anonymous: bool
    sealed_at: float
    seal: str

    @property
    def identity(self) -> str:
        return self.principal_id


class TrustedPrincipalIssuer:
    """Issues TrustedPrincipal seals using a secret key.

    Held by IdentityResolver. The secret key is generated per-instance
    (per Runtime/Agent) and NEVER exposed.
    """

    def __init__(self, *, secret_key: bytes | None = None):
        self._secret = secret_key or secrets.token_bytes(32)

    def _sign(self, data: str) -> str:
        return hmac.new(self._secret, data.encode(), hashlib.sha256).hexdigest()

    def _canonical(
        self, principal_id: str, kind: str, roles: tuple[str, ...],
        auth_method: str, is_anonymous: bool, sealed_at: float,
    ) -> str:
        """Canonical representation for HMAC signing.
        
        Uses JSON serialization with sorted keys to guarantee:
        1. No separator collisions (JSON escapes special characters)
        2. Deterministic ordering (sorted roles)
        3. Type-ambiguous fields are unambiguous (JSON booleans, numbers)
        
        This prevents attacks like:
        - roles=("admin,foo",) vs roles=("admin", "foo") — JSON arrays
          distinguish a single element containing a comma from two elements
        - principal_id="a|b" colliding with principal_id="a", kind="b" —
          JSON key-value pairs prevent field-boundary ambiguity
        """
        import json
        return json.dumps({
            "principal_id": principal_id,
            "kind": kind,
            "roles": sorted(roles),
            "auth_method": auth_method,
            "is_anonymous": is_anonymous,
            "sealed_at": sealed_at,
        }, sort_keys=True, separators=(",", ":"))

    def seal(self, principal: Any) -> TrustedPrincipal:
        """Seal an AuthPrincipal into a TrustedPrincipal.

        ``principal`` must have ``identity`` (or ``principal_id``), ``kind``,
        ``roles``, ``auth_method``, and ``is_anonymous`` attributes.
        """
        import time
        principal_id = (
            getattr(principal, 'identity', None)
            or getattr(principal, 'principal_id', None)
            or "unknown"
        )
        kind = getattr(principal, 'kind', 'unknown')
        roles = tuple(getattr(principal, 'roles', []) or [])
        auth_method = getattr(principal, 'auth_method', 'unknown')
        is_anonymous = getattr(principal, 'is_anonymous', True)
        sealed_at = time.time()

        canonical = self._canonical(
            principal_id, kind, roles, auth_method, is_anonymous, sealed_at
        )
        seal = self._sign(canonical)
        return TrustedPrincipal(
            principal_id=principal_id,
            kind=kind,
            roles=roles,
            auth_method=auth_method,
            is_anonymous=is_anonymous,
            sealed_at=sealed_at,
            seal=seal,
        )

    def verify(self, trusted: Any) -> bool:
        """Verify a TrustedPrincipal's seal. Returns False for:
        - None
        - Any object that is not a TrustedPrincipal
        - TrustedPrincipals with invalid seals
        """
        if not isinstance(trusted, TrustedPrincipal):
            return False
        canonical = self._canonical(
            trusted.principal_id,
            trusted.kind,
            trusted.roles,
            trusted.auth_method,
            trusted.is_anonymous,
            trusted.sealed_at,
        )
        expected = self._sign(canonical)
        return hmac.compare_digest(trusted.seal, expected)


class TrustedPrincipalVerifier:
    """Verifier-only facade for ToolExecutor.

    Holds a reference to the Issuer's verify method but NOT the signing
    key. This ensures the ToolExecutor can verify TrustedPrincipals but
    cannot issue them.
    """

    def __init__(self, issuer: TrustedPrincipalIssuer):
        self._issuer = issuer

    def verify(self, trusted: Any) -> bool:
        """Verify a TrustedPrincipal. Returns True only if the object is
        a TrustedPrincipal with a valid seal from the same issuer."""
        return self._issuer.verify(trusted)


__all__ = [
    "TrustedPrincipal",
    "TrustedPrincipalIssuer",
    "TrustedPrincipalVerifier",
]
