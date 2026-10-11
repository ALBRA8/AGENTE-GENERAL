"""A2A subpackage — Agent2Agent protocol adapter on top of Handoff.

The A2A package is a TRANSLATION LAYER. It never duplicates Handoff,
Execution, MCP, Evidence, Provenance or Audit. The single source of
truth for an agent's identity is the `AgentManifest`; the `AgentCard`
exposed via A2A is DERIVED from it.

CORE_ONLY MODE: the InProcessTransport is always available; no httpx /
network dependency is required for the full A2A flow to work.
"""

from albra_seed.a2a.adapter import A2AAdapter
from albra_seed.a2a.card import A2ACardSkill, AgentCard, manifest_to_card
from albra_seed.a2a.client import A2AClient
from albra_seed.a2a.contract import (
    A2AArtifact,
    A2AMessage,
    A2ATask,
    A2ATaskStatus,
    TaskState,
)
from albra_seed.a2a.registry import A2AAgentRecord, A2ARegistry
from albra_seed.a2a.security import (
    A2A_RECEIVE_ACTION,
    A2ASecurityDecision,
    A2ASecurityFilter,
)
from albra_seed.a2a.server import A2AServer, HandoffHandlerFn
from albra_seed.a2a.transport import (
    A2ATransport,
    HTTPTransport,
    InProcessTransport,
)

__all__ = [
    # card
    "A2ACardSkill",
    "AgentCard",
    "manifest_to_card",
    # contract
    "TaskState",
    "A2AMessage",
    "A2AArtifact",
    "A2ATask",
    "A2ATaskStatus",
    # adapter
    "A2AAdapter",
    # security
    "A2ASecurityFilter",
    "A2ASecurityDecision",
    "A2A_RECEIVE_ACTION",
    # transport
    "A2ATransport",
    "InProcessTransport",
    "HTTPTransport",
    # server / client / registry
    "A2AServer",
    "HandoffHandlerFn",
    "A2AClient",
    "A2ARegistry",
    "A2AAgentRecord",
]
