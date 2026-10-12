"""A2A interoperability test fixtures.

This package contains standalone A2A 1.0 reference fixtures used to verify
that ALBRA's HTTPTransport + A2AClient can interoperate with non-ALBRA
A2A 1.0-compliant agents.

The fixtures intentionally DO NOT use ALBRA's A2A client/transport
classes for the agent side — they speak raw JSON-RPC 2.0 over HTTP via
FastAPI so the interop surface is genuinely external.
"""

from tests.a2a.fixtures.remote_a2a_agent import build_remote_a2a_agent_app

__all__ = ["build_remote_a2a_agent_app"]
