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

from albra_seed.core.types import (
    Message,
    Role,
    ToolCall,
    ToolResult,
    AgentResponse,
    Session,
    Severity,
    RiskLevel,
    AutonomyLevel,
    ExecutionStatus,
    EvidenceType,
    MemoryType,
    KnowledgeType,
    ComponentStatus,
    PolicyDecision,
    Evidence,
    Provenance,
    RunContext,
    ApprovalStatus,
    HealthState,
    CommunicationChannel,
    ModalityType,
)
from albra_seed.core.identity import AgentIdentity, AgentManifest
from albra_seed.core.config import SeedConfig, AgentConfig
from albra_seed.core.base import (
    Tool,
    MemoryStore,
    LLMProvider,
    Guardrail,
    Skill,
    Workflow,
    Plugin,
    Capability,
)
from albra_seed.runtime.agent import Agent
from albra_seed.runtime.runtime import Runtime


def __getattr__(name: str):  # pragma: no cover - lazy public API
    """Lazy-access optional exports so the package stays CORE_ONLY safe.

    `create_app` requires fastapi; `main` requires only stdlib (argparse).
    Both are imported on first attribute access.
    """
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
    # Lazy exports (resolved on first access via __getattr__):
    "create_app",
    "main",
    "ConfigManager",
]
