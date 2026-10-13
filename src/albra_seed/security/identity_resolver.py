"""IdentityResolver — resolves AuthPrincipal and seals it as TrustedPrincipal.

The resolver authenticates via the runtime's Authenticator, then wraps
the result in a ``TrustedPrincipal`` — a sealed object that callers
CANNOT forge. The seal is an HMAC-SHA256 computed with a per-resolver
secret key.

``RunContext.verified_principal`` now holds a ``TrustedPrincipal`` (NOT
a raw ``AuthPrincipal``). Callers who set ``verified_principal`` to an
arbitrary ``AuthPrincipal`` will fail the seal verification in
``ToolExecutor``, which uses ``TrustedPrincipalVerifier`` to check the
seal before extracting ``principal_id``.

DEFAULT-DENY: if no Authenticator is configured, an anonymous principal
is sealed. The PolicyEngine DENIES protected actions for anonymous
principals (``actor_roles=[]``).

Fail-closed: if the Authenticator raises, ``None`` is returned and
``verified_principal`` is set to ``None``. The ToolExecutor rejects
any execution where ``verified_principal`` is not a valid
``TrustedPrincipal``.
"""

from __future__ import annotations

from albra_seed.core.types import RunContext
from albra_seed.security.auth import (
    Authenticator,
    AuthPrincipal,
)
from albra_seed.security.trusted_principal import (
    TrustedPrincipal,
    TrustedPrincipalIssuer,
    TrustedPrincipalVerifier,
)


class IdentityResolver:
    """Resolves AuthPrincipal, seals it as TrustedPrincipal, attaches to RunContext.

    The resolver holds a ``TrustedPrincipalIssuer`` (with a secret key)
    that seals the authenticated principal. Callers who set
    ``run_ctx.verified_principal`` to a raw ``AuthPrincipal`` will NOT
    pass the ToolExecutor's seal verification — only a
    ``TrustedPrincipal`` issued by this resolver's issuer is accepted.
    """

    def __init__(
        self,
        authenticator: Authenticator | None = None,
        *,
        issuer: TrustedPrincipalIssuer | None = None,
    ) -> None:
        self._authenticator: Authenticator | None = authenticator
        self._issuer: TrustedPrincipalIssuer = issuer or TrustedPrincipalIssuer()

    @property
    def trusted_verifier(self) -> TrustedPrincipalVerifier:
        """Return a TrustedPrincipalVerifier for ToolExecutor.

        The verifier can verify TrustedPrincipals sealed by this resolver
        but CANNOT issue new ones (no signing key exposed).
        """
        return TrustedPrincipalVerifier(self._issuer)

    async def resolve_and_attach(
        self,
        run_ctx: RunContext,
        *,
        token: str | None = None,
    ) -> TrustedPrincipal | None:
        """Resolve principal from token, seal it, and attach to RunContext.

        Returns the TrustedPrincipal if authentication succeeds, None
        otherwise. The TrustedPrincipal is attached to
        ``run_ctx.verified_principal``.

        When no Authenticator is configured (CORE_ONLY mode), an
        anonymous principal is sealed. This is safe because the
        PolicyEngine DENIES protected actions for empty roles.
        """
        auth = self._authenticator
        if auth is None:
            principal = AuthPrincipal(
                kind="anonymous",
                identity="anonymous",
                roles=[],
                is_anonymous=True,
                auth_method="none",
            )
        else:
            try:
                principal = await auth.verify(token)
            except Exception:  # noqa: BLE001
                principal = None

        if principal is None:
            run_ctx.verified_principal = None
            return None

        # Seal the principal — callers cannot forge this
        trusted = self._issuer.seal(principal)
        run_ctx.verified_principal = trusted
        return trusted

    def get_principal(self, run_ctx: RunContext) -> TrustedPrincipal | None:
        """Get the verified TrustedPrincipal from a RunContext, if set."""
        vp = getattr(run_ctx, "verified_principal", None)
        if isinstance(vp, TrustedPrincipal):
            return vp
        return None


__all__ = ["IdentityResolver"]
