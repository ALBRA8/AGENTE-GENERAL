"""IdentityResolver — resolves AuthPrincipal and attaches to RunContext.

SEC-PERIMETER (Hallazgo 2): the principal is resolved from the
runtime's Authenticator (if configured) and stored in
``RunContext.verified_principal`` — a dedicated field that can ONLY be
set by the IdentityResolver after authentication. Callers CANNOT set
it via ``RunContext.metadata`` (which is public/writable and would
allow role impersonation).

DEFAULT-DENY: if no Authenticator is configured, the resolver returns
an anonymous principal (``is_anonymous=True``, ``roles=[]``). The
PolicyEngine will then DENY any action requiring a permission
(``has_permission=False`` when ``actor_roles is None`` or empty —
see ``albra_seed.policy.engine``).

Fail-closed: if the Authenticator raises, the resolver returns
``None`` (NOT an anonymous principal). The PolicyEngine treats
``principal is None`` as ``actor_roles=None`` → DENY for any protected
action. The Authenticator's own contract is to return ``None`` (never
raise) on invalid tokens, but we wrap the call in try/except for
defense-in-depth in case a specialization breaks that contract.

Authentication ≠ Authorization: this module answers WHO. The
PolicyEngine + PermissionRegistry answer WHAT they may do.
"""

from __future__ import annotations

from albra_seed.core.types import RunContext
from albra_seed.security.auth import (
    Authenticator,
    AuthPrincipal,
)


class IdentityResolver:
    """Resolves AuthPrincipal from the runtime's Authenticator.

    The resolved principal is attached to the ``RunContext`` via the
    ``verified_principal`` field. This field is NOT settable by callers
    via ``metadata`` — it's a private field that only the IdentityResolver
    populates after authenticating. The Agent loop constructs an
    IdentityResolver per-run, resolves the principal ONCE at the start of
    ``_phase_check_permissions``, and reuses it for every tool-call
    decision in that phase.
    """

    def __init__(self, authenticator: Authenticator | None = None) -> None:
        self._authenticator: Authenticator | None = authenticator

    async def resolve_and_attach(
        self,
        run_ctx: RunContext,
        *,
        token: str | None = None,
    ) -> AuthPrincipal | None:
        """Resolve principal from token + attach to RunContext.

        Returns the AuthPrincipal if authentication succeeds, None
        otherwise (Authenticator raised — fail-closed). The principal
        is attached to ``run_ctx.verified_principal``.

        When no Authenticator is configured (CORE_ONLY mode), an
        anonymous principal is returned (``is_anonymous=True``,
        ``roles=[]``). This is safe because the PolicyEngine DENIES
        any protected action when ``actor_roles`` is empty / None.
        """
        auth = self._authenticator
        if auth is None:
            # No authenticator → anonymous principal (CORE_ONLY safe).
            # PolicyEngine will deny actions requiring permissions.
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
                # Fail-closed: any Authenticator exception → None.
                # The Authenticator's contract is to return None (never
                # raise) on invalid tokens, but defense-in-depth requires
                # we tolerate specialization layers that break it.
                principal = None

        # Attach to RunContext. The verified_principal field is NOT
        # in metadata (which is public/writable) — it's a dedicated field.
        run_ctx.verified_principal = principal
        return principal

    def get_principal(self, run_ctx: RunContext) -> AuthPrincipal | None:
        """Get the verified principal from a RunContext, if set.

        Returns None if the IdentityResolver has not been invoked on
        this RunContext (e.g. the caller bypassed the agent loop).
        """
        return getattr(run_ctx, "verified_principal", None)


__all__ = ["IdentityResolver"]
