"""AuthorizationToken — non-forgeable execution authorization.

An AuthorizationToken is the ONLY object that ToolExecutor accepts as
proof that a tool call was authorized. It is:

1. **Cryptographically signed**: the token carries an HMAC-SHA256 signature
   over (tool_name + principal_id + execution_id + context_id + timestamp).
   The signing key is known ONLY to the PolicyEngine (or a dedicated
   AuthorizationIssuer). A caller cannot fabricate a valid token without
   the signing key.

2. **Bound to the call**: the token carries ``tool_name``, ``principal_id``,
   ``execution_id``, and ``context_id``. The executor verifies that the
   token's tool_name matches the requested tool, the principal matches
   the RunContext's verified_principal, and the execution_id matches.

3. **Expiry-aware**: tokens have a ``expires_at`` timestamp. Expired tokens
   are rejected.

4. **Revocable**: tokens carry a ``token_id`` that can be added to a
   revocation set. Revoked tokens are rejected even if the signature
   is valid and they have not expired.

This module does NOT replace PolicyEngine — it wraps the PolicyVerdict
into a non-forgeable token that the executor can verify independently.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuthorizationToken:
    """Non-forgeable authorization for a single tool execution.

    Created by AuthorizationIssuer.issue() — callers CANNOT construct
    a valid token directly (the constructor is public but without the
    correct ``signature`` field, ``verify()`` will fail).

    Fields:
        tool_name: the tool this token authorizes (must match executor's name)
        principal_id: identity of the authenticated principal
        execution_id: RunContext.execution_id this token is bound to
        context_id: RunContext.context_id (or "" if None)
        issued_at: unix timestamp when issued
        expires_at: unix timestamp when the token expires
        token_id: unique id for revocation tracking
        signature: HMAC-SHA256 over all fields above
    """

    tool_name: str
    principal_id: str
    execution_id: str
    context_id: str
    issued_at: float
    expires_at: float
    token_id: str
    signature: str

    def is_expired(self, now: float | None = None) -> bool:
        """Check if the token has expired."""
        ts = now if now is not None else time.time()
        return ts >= self.expires_at

    def matches(
        self,
        *,
        tool_name: str,
        execution_id: str,
        principal_id: str | None = None,
        context_id: str | None = None,
    ) -> bool:
        """Check if the token matches the given execution context."""
        if self.tool_name != tool_name:
            return False
        if self.execution_id != execution_id:
            return False
        if principal_id is not None and self.principal_id != principal_id:
            return False
        if context_id is not None and self.context_id != context_id:
            return False
        return True


class AuthorizationIssuer:
    """Issues and verifies AuthorizationTokens using a secret signing key.

    The signing key is generated per-Issuer instance (per Runtime).
    Callers who obtain an Issuer reference can issue tokens, but callers
    who only have the ToolExecutor CANNOT issue tokens — they can only
    verify them (the executor has a Verifier, not an Issuer).

    Architecture:
        PolicyEngine → AuthorizationIssuer.issue(verdict, tool, ctx, principal)
                     → AuthorizationToken (signed)
        ToolExecutor → AuthorizationVerifier.verify(token, tool, ctx, principal)
                     → bool (valid / invalid)
    """

    def __init__(self, *, secret_key: bytes | None = None, ttl_seconds: float = 60.0):
        self._secret = secret_key or self._generate_key()
        self._ttl = ttl_seconds
        self._revoked: set[str] = set()

    @staticmethod
    def _generate_key() -> bytes:
        import secrets
        return secrets.token_bytes(32)

    def _sign(self, data: str) -> str:
        return hmac.new(self._secret, data.encode(), hashlib.sha256).hexdigest()

    def _canonical(
        self,
        tool_name: str,
        principal_id: str,
        execution_id: str,
        context_id: str,
        issued_at: float,
        expires_at: float,
        token_id: str,
    ) -> str:
        return f"{tool_name}|{principal_id}|{execution_id}|{context_id}|{issued_at}|{expires_at}|{token_id}"

    def issue(
        self,
        *,
        tool_name: str,
        principal_id: str,
        execution_id: str,
        context_id: str | None = None,
        ttl_seconds: float | None = None,
    ) -> AuthorizationToken:
        """Issue a new authorization token bound to the given context."""
        import uuid
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        token_id = uuid.uuid4().hex
        expires_at = now + ttl
        ctx_id = context_id or ""
        canonical = self._canonical(
            tool_name, principal_id, execution_id, ctx_id, now, expires_at, token_id
        )
        signature = self._sign(canonical)
        return AuthorizationToken(
            tool_name=tool_name,
            principal_id=principal_id,
            execution_id=execution_id,
            context_id=ctx_id,
            issued_at=now,
            expires_at=expires_at,
            token_id=token_id,
            signature=signature,
        )

    def revoke(self, token_id: str) -> None:
        """Revoke a token by its id."""
        self._revoked.add(token_id)

    def is_revoked(self, token_id: str) -> bool:
        return token_id in self._revoked

    def verify(
        self,
        token: AuthorizationToken,
        *,
        tool_name: str,
        execution_id: str,
        principal_id: str | None = None,
        context_id: str | None = None,
        now: float | None = None,
    ) -> bool:
        """Verify a token is valid, not expired, not revoked, and matches.

        This is the method ToolExecutor calls (via the verifier) before
        executing any non-dry-run tool.
        """
        # 1. Check signature
        canonical = self._canonical(
            token.tool_name,
            token.principal_id,
            token.execution_id,
            token.context_id,
            token.issued_at,
            token.expires_at,
            token.token_id,
        )
        expected_sig = self._sign(canonical)
        if not hmac.compare_digest(token.signature, expected_sig):
            return False

        # 2. Check expiry
        if token.is_expired(now):
            return False

        # 3. Check revocation
        if self.is_revoked(token.token_id):
            return False

        # 4. Check binding (tool + execution + principal + context)
        if not token.matches(
            tool_name=tool_name,
            execution_id=execution_id,
            principal_id=principal_id,
            context_id=context_id,
        ):
            return False

        return True


class AuthorizationVerifier:
    """Verifier-only facade for ToolExecutor.

    Holds a reference to the Issuer's verify method but NOT the signing
    key. This ensures the ToolExecutor can verify tokens but cannot
    issue them.
    """

    def __init__(self, issuer: AuthorizationIssuer):
        self._issuer = issuer

    def verify(
        self,
        token: Any,
        *,
        tool_name: str,
        execution_id: str,
        principal_id: str | None = None,
        context_id: str | None = None,
        now: float | None = None,
    ) -> bool:
        """Verify a token. Returns True only if the token is a valid
        AuthorizationToken, correctly signed, not expired, not revoked,
        and bound to the given (tool_name, execution_id, principal_id, context_id).

        Returns False for:
        - None
        - Any object that is not an AuthorizationToken
        - Tokens with invalid signatures
        - Expired tokens
        - Revoked tokens
        - Tokens bound to a different tool/execution/principal/context
        """
        if not isinstance(token, AuthorizationToken):
            return False
        return self._issuer.verify(
            token,
            tool_name=tool_name,
            execution_id=execution_id,
            principal_id=principal_id,
            context_id=context_id,
            now=now,
        )


__all__ = [
    "AuthorizationToken",
    "AuthorizationIssuer",
    "AuthorizationVerifier",
]
