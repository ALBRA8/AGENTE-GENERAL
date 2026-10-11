"""Abstract base classes & shared contracts.

These are the SEED's universal interfaces. Every concrete implementation
(provider, tool, skill, ...) must inherit from these. This guarantees a
stable surface for specialization layers to build against.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import (
    AgentResponse,
    AutonomyLevel,
    Evidence,
    ExecutionStatus,
    MemoryType,
    Message,
    RiskLevel,
    RunContext,
    ToolCall,
    ToolResult,
    new_id,
    utc_now,
)


# =====================================================================
# LLM PROVIDER
# =====================================================================


class LLMProvider(ABC):
    """Universal LLM provider interface.

    The runtime NEVER depends on a concrete provider. MockProvider is always
    available so the seed can run in CORE_ONLY mode without external credentials.
    """

    name: str = "base"
    is_mock: bool = False
    is_external: bool = False

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
        context: RunContext | None = None,
    ) -> AgentResponse:
        """Generate a completion. Returns an AgentResponse."""
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> bool:
        raise NotImplementedError


# =====================================================================
# TOOL
# =====================================================================


class ToolContract(BaseModel):
    """Formal contract describing a tool. Required at registration time."""

    tool_id: str = Field(default_factory=lambda: new_id("tool"))
    name: str
    description: str
    version: str = "1.0.0"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.LOW
    permissions: list[str] = Field(default_factory=list)
    timeout_seconds: int = 30
    retry_policy: dict[str, Any] = Field(default_factory=lambda: {"max": 0})
    evidence_required: bool = False
    is_dry_run_capable: bool = True
    is_external: bool = False
    availability: str = "available"


class Tool(ABC):
    """Universal tool interface. Specialized tools are added by specialization
    layers. The SEED only ships infra tools (echo, health, no-op)."""

    contract: ToolContract

    @abstractmethod
    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        context: RunContext | None = None,
        dry_run: bool = False,
    ) -> ToolResult:
        raise NotImplementedError


# =====================================================================
# CAPABILITY  vs  SKILL  vs  KNOWLEDGE  vs  MEMORY  vs  PERMISSION
# (kept as separate concepts — never conflated)
# =====================================================================


class Capability(BaseModel):
    """A capability describes WHAT an agent can technically do.
    A skill describes HOW to do it correctly.
    Knowledge describes WHAT it knows.
    Memory describes WHAT it remembers.
    Permission describes WHAT it's authorized to do."""

    capability_id: str = Field(default_factory=lambda: new_id("cap"))
    name: str
    description: str
    version: str = "1.0.0"
    requires_tools: list[str] = Field(default_factory=list)
    requires_providers: list[str] = Field(default_factory=list)
    requires_permissions: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    is_external: bool = False


class Skill(BaseModel):
    """A skill is the formal description of a procedure. Specialization layers
    register skills; the SEED ships none."""

    skill_id: str = Field(default_factory=lambda: new_id("skill"))
    name: str
    version: str = "1.0.0"
    objective: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    evidence_required: bool = False
    tests: list[str] = Field(default_factory=list)
    status: ExecutionStatus = ExecutionStatus.PENDING


class Workflow(BaseModel):
    """A workflow is a graph of steps. The WorkflowEngine executes them."""

    workflow_id: str = Field(default_factory=lambda: new_id("wf"))
    name: str
    version: str = "1.0.0"
    steps: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW


class Plugin(BaseModel):
    """A plugin contributes tools/skills/providers/interfaces without modifying
    the core. Installed via the PluginManager."""

    plugin_id: str = Field(default_factory=lambda: new_id("plugin"))
    name: str
    version: str = "1.0.0"
    contributes_tools: list[str] = Field(default_factory=list)
    contributes_skills: list[str] = Field(default_factory=list)
    contributes_providers: list[str] = Field(default_factory=list)
    contributes_interfaces: list[str] = Field(default_factory=list)
    enabled: bool = True


# =====================================================================
# GUARDRAIL
# =====================================================================


class Guardrail(ABC):
    """A guardrail validates input or output. Default deny on rejection."""

    name: str = "base"

    @abstractmethod
    async def check_input(self, message: Message, *, context: RunContext | None = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def check_output(self, response: AgentResponse, *, context: RunContext | None = None) -> bool:
        raise NotImplementedError


# =====================================================================
# MEMORY STORE — MemoryDV
# =====================================================================


class MemoryRecord(BaseModel):
    """A single memory record with full provenance."""

    record_id: str = Field(default_factory=lambda: new_id("mem"))
    agent_id: str
    domain: str = "default"
    user_id: str | None = None
    context_id: str | None = None
    memory_type: MemoryType
    content: Any
    confidence: float = 0.0
    source: str = "agent"
    source_type: str = "agent"
    evidence_ids: list[str] = Field(default_factory=list)
    provenance_id: str | None = None
    execution_id: str | None = None
    correlation_id: str | None = None
    version: int = 1
    created_at: Any = Field(default_factory=utc_now)
    modified_at: Any = Field(default_factory=utc_now)
    is_fact: bool = False  # True only if evidence_type == FACT and confidence >= threshold


class MemoryStore(ABC):
    """MemoryDV — universal memory store. Five memory types supported:
    episodic / semantic / factual / procedural / working.
    Memory must be isolatable by agent_id + domain + user/context.
    """

    @abstractmethod
    async def write(self, record: MemoryRecord) -> str:
        raise NotImplementedError

    @abstractmethod
    async def read(self, record_id: str) -> MemoryRecord | None:
        raise NotImplementedError

    @abstractmethod
    async def query(
        self,
        *,
        agent_id: str,
        domain: str = "default",
        user_id: str | None = None,
        context_id: str | None = None,
        memory_type: MemoryType | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, record_id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def count(self, *, agent_id: str, domain: str = "default") -> int:
        raise NotImplementedError
