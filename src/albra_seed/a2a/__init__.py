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
from albra_seed.a2a.exposition import build_a2a_server
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
from albra_seed.a2a.wire import (
    agent_card_to_wire,
    artifact_to_wire,
    make_jsonrpc_request,
    make_jsonrpc_response,
    message_to_wire,
    task_to_wire,
    wire_to_agent_card,
    wire_to_artifact,
    wire_to_message,
    wire_to_task,
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
    # server / client / registry / exposition
    "A2AServer",
    "HandoffHandlerFn",
    "A2AClient",
    "A2ARegistry",
    "A2AAgentRecord",
    "build_a2a_server",
    # wire format
    "agent_card_to_wire",
    "wire_to_agent_card",
    "message_to_wire",
    "wire_to_message",
    "artifact_to_wire",
    "wire_to_artifact",
    "task_to_wire",
    "wire_to_task",
    "make_jsonrpc_request",
    "make_jsonrpc_response",
]
