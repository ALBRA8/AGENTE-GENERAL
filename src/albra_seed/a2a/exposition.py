"""A2A exposition helper — builds a fully-wired A2AServer ready to be
exposed via FastAPI.

This is a convenience module: it composes the existing ``A2AServer`` +
``HandoffManager`` + ``A2AAdapter`` + ``A2ASecurityFilter`` into a
single call so application code can do:

    server = build_a2a_server(runtime, identity=my_identity)
    app = create_app(runtime, a2a_server=server)

The helper carries NO domain knowledge — it is pure composition on top
of existing components. It does NOT start the runtime (caller's
responsibility) and does NOT register the server with the FastAPI app
(that happens inside ``create_app`` when ``a2a_server`` is passed).
"""

from __future__ import annotations

from typing import Any

from albra_seed.a2a.server import A2AServer, HandoffHandlerFn
from albra_seed.core.identity import AgentIdentity
from albra_seed.handoff.manager import HandoffManager


def build_a2a_server(
    runtime: Any,
    *,
    identity: AgentIdentity,
    endpoint: str | None = None,
    handler: HandoffHandlerFn | None = None,
    handoff_manager: HandoffManager | None = None,
) -> A2AServer:
    """Build a fully-wired ``A2AServer``.

    Parameters
    ----------
    runtime : Runtime
        The ALBRA runtime (must be started; the server reads audit /
        provenance / evidence / security components from it).
    identity : AgentIdentity
        The agent identity exposed via the well-known agent card.
    endpoint : str | None
        Optional override for the agent card's ``url`` field. Defaults
        to ``identity.endpoint``. Stored on the returned server as the
        public ``endpoint`` attribute for the well-known route.
    handler : HandoffHandlerFn | None
        Optional custom handoff handler. If None, the default echo
        handler is used (CORE_ONLY-safe).
    handoff_manager : HandoffManager | None
        Optional pre-built HandoffManager. If None, a fresh one is
        constructed.
    """
    mgr = handoff_manager or HandoffManager()
    server = A2AServer(
        runtime,
        identity=identity,
        handoff_manager=mgr,
        handler=handler,
    )
    # Surface the public endpoint as an attribute so the well-known
    # route handler (and callers) can read it without inspecting the
    # manifest. This is additive — A2AServer does not define this
    # attribute natively, but assigning it does not break anything.
    server.endpoint = endpoint or identity.endpoint  # type: ignore[attr-defined]
    return server


__all__ = ["build_a2a_server"]
