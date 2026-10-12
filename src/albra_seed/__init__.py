"""
ALBRA AGENT SEED V1.0
====================

Universal DNA for the ALBRA agent ecosystem.

This package contains ONLY transversal infrastructure — no domain knowledge.
Specialized agents (legal, sales, research, trading, ...) are built by
cloning this seed and adding specialization on top, without modifying the core.

Public API:
    from albra_seed import Agent, AgentConfig, Message, Tool, MemoryStore

Layers (each in its own subpackage):
    core            — types, base abstractions, identity, config
    runtime         — lifecycle, agent loop
    reasoning       — LLM abstraction, planner, prompts
    providers       — provider registry, router, mock
    memory          — MemoryDV: episodic/semantic/factual/procedural/working
    security        — auth, permissions, secrets
    firewall        — action firewall (default deny)
    policy          — policy engine
    evidence        — evidence contract
    provenance      — provenance tracking
    execution       — runner, traces, idempotency
    audit           — structured audit log + tamper-evident chain
    mcp             — MCP server/client interfaces
    handoff         — agent-to-agent handoff contract
    tools           — tool registry
    capabilities    — capability registry
    skills          — skill registry
    plugins         — plugin architecture
    communication   — communication router (text/voice/image/doc)
    multimodal      — multimodal adapters
    files           — file/document handling
    workflows       — workflow engine + graph
    jobs            — job manager
    scheduler       — task scheduler
    resilience      — retry, timeout, fallback, circuit breaker
    observability   — structured logs, metrics, traces
    autonomy        — autonomy levels (L0..L5)
    approvals       — HITL approval engine
    doctor          — diagnostics
    api             — REST API
    cli             — CLI
    config          — centralized configuration
"""

from albra_seed.core.base import (
    Capability,
    Guardrail,
    LLMProvider,
    MemoryStore,
    Plugin,
    Skill,
    Tool,
    Workflow,
)
from albra_seed.core.config import AgentConfig, SeedConfig
from albra_seed.core.identity import AgentIdentity, AgentManifest
from albra_seed.core.types import (
    AgentResponse,
    ApprovalStatus,
    AutonomyLevel,
    CommunicationChannel,
    ComponentStatus,
    Evidence,
    EvidenceType,
    ExecutionStatus,
    HealthState,
    KnowledgeType,
    MemoryType,
    Message,
    ModalityType,
    PolicyDecision,
    Provenance,
    RiskLevel,
    Role,
    RunContext,
    Session,
    Severity,
    ToolCall,
    ToolResult,
)
from albra_seed.feedback import (
    FeedbackManager,
    FeedbackRecord,
    FeedbackStore,
    FeedbackType,
)
from albra_seed.learning import (
    LearningManager,
    LearningProposal,
    LearningSignal,
    LearningStore,
    ProposalGenerator,
    ProposalStatus,
)
from albra_seed.memory import (
    DecisionMemory,
    DecisionRecord,
    MemoryRetriever,
    RetrievalConfig,
    RetrievedMemory,
)
from albra_seed.runtime.agent import Agent
from albra_seed.runtime.runtime import Runtime
from albra_seed.security.identity_resolver import IdentityResolver
from albra_seed.skills import (
    ImprovementProposal,
    SkillEvolutionError,
    SkillEvolutionManager,
    SkillMetrics,
)


def __getattr__(name: str):  # pragma: no cover - lazy public API
    """Lazy-access optional exports so the package stays CORE_ONLY safe.

    `create_app` requires fastapi; `main` requires only stdlib (argparse).
    Both are imported on first attribute access.

    A2A exports (AgentCard, A2ATask, A2AAdapter, ...) are also lazy so
    that importing `albra_seed` in CORE_ONLY mode does not pull in the
    A2A subpackage (which is purely additive).
    """
    _A2A_EXPORTS = {
        "AgentCard",
        "A2ACardSkill",
        "manifest_to_card",
        "TaskState",
        "A2AMessage",
        "A2AArtifact",
        "A2ATask",
        "A2ATaskStatus",
        "A2AAdapter",
        "A2ASecurityFilter",
        "A2ASecurityDecision",
        "A2ATransport",
        "InProcessTransport",
        "HTTPTransport",
        "A2AServer",
        "A2AClient",
        "A2ARegistry",
        "A2AAgentRecord",
        # wire format + exposition (added by A2A-GAP-A)
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
        "build_a2a_server",
    }
    if name in _A2A_EXPORTS:
        from albra_seed import a2a as _a2a

        return getattr(_a2a, name)
    if name == "create_app":
        from albra_seed.api.app import create_app
        return create_app
    if name == "main":
        from albra_seed.cli.cli import main
        return main
    if name == "ConfigManager":
        from albra_seed.config import ConfigManager
        return ConfigManager
    raise AttributeError(f"module 'albra_seed' has no attribute {name!r}")


__version__ = "1.0.0"
__seed_version__ = "ALBRA_SEED_V1.0"
__runtime_version__ = "1.0.0"

__all__ = [
    "Agent",
    "Runtime",
    "AgentIdentity",
    "AgentManifest",
    "SeedConfig",
    "AgentConfig",
    "Message",
    "Role",
    "ToolCall",
    "ToolResult",
    "AgentResponse",
    "Session",
    "Severity",
    "RiskLevel",
    "AutonomyLevel",
    "ExecutionStatus",
    "EvidenceType",
    "MemoryType",
    "KnowledgeType",
    "ComponentStatus",
    "PolicyDecision",
    "Evidence",
    "Provenance",
    "RunContext",
    "ApprovalStatus",
    "HealthState",
    "CommunicationChannel",
    "ModalityType",
    "Tool",
    "MemoryStore",
    "LLMProvider",
    "Guardrail",
    "Skill",
    "Workflow",
    "Plugin",
    "Capability",
    "DecisionRecord",
    "DecisionMemory",
    # MEM-READ: deterministic memory retrieval (lexical + structural).
    "MemoryRetriever",
    "RetrievalConfig",
    "RetrievedMemory",
    "FeedbackManager",
    "FeedbackRecord",
    "FeedbackStore",
    "FeedbackType",
    "LearningManager",
    "LearningProposal",
    "LearningSignal",
    "LearningStore",
    "ProposalGenerator",
    "ProposalStatus",
    "SkillMetrics",
    "ImprovementProposal",
    "SkillEvolutionManager",
    "SkillEvolutionError",
    # SEC-PERIMETER: identity resolver for the unified security perimeter.
    "IdentityResolver",
    # Lazy exports (resolved on first access via __getattr__):
    "create_app",
    "main",
    "ConfigManager",
    # A2A exports (resolved on first access via __getattr__; lazy so
    # CORE_ONLY mode is not impacted by the additive A2A subpackage):
    "AgentCard",
    "A2ACardSkill",
    "manifest_to_card",
    "TaskState",
    "A2AMessage",
    "A2AArtifact",
    "A2ATask",
    "A2ATaskStatus",
    "A2AAdapter",
    "A2ASecurityFilter",
    "A2ASecurityDecision",
    "A2ATransport",
    "InProcessTransport",
    "HTTPTransport",
    "A2AServer",
    "A2AClient",
    "A2ARegistry",
    "A2AAgentRecord",
    # wire format + exposition (added by A2A-GAP-A)
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
    "build_a2a_server",
]
