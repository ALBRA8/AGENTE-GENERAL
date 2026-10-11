"""Agent identity — formal, versioned, independent of specialization.

Identity describes WHAT the agent is, not WHAT it knows. Specialization layers
add domain identity on top, but the base identity is domain-agnostic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.constants import RUNTIME_VERSION, SEED_VERSION
from albra_seed.core.types import AutonomyLevel, RiskLevel, new_id, utc_now


class AgentIdentity(BaseModel):
    """Formal identity of an ALBRA agent. Independent of specialization."""

    agent_id: str = Field(default_factory=lambda: new_id("agent"))
    name: str = "albra-agent"
    version: str = "1.0.0"
    seed_version: str = SEED_VERSION
    runtime_version: str = RUNTIME_VERSION
    capabilities: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    autonomy_level: AutonomyLevel = AutonomyLevel.L0_OBSERVE
    risk_profile: RiskLevel = RiskLevel.LOW
    providers: list[str] = Field(default_factory=lambda: ["mock"])
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    memory_domains: list[str] = Field(default_factory=lambda: ["default"])
    interfaces: list[str] = Field(default_factory=lambda: ["text"])
    dependencies: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    specialization: str | None = None  # None == raw seed
    metadata: dict[str, Any] = Field(default_factory=dict)
    # A2A discovery endpoint (added by GAP-A2A-IMPL, NON-BREAKING):
    # when populated, the agent is reachable at this URL via the A2A protocol.
    # Remains None in CORE_ONLY mode (no network transport needed).
    endpoint: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    def to_manifest(self) -> AgentManifest:
        return AgentManifest(identity=self)


class AgentManifest(BaseModel):
    """Agent Manifest — discoverable description used by external systems.

    A manifest must be sufficient for another system to know:
        - what the agent is
        - what it can / cannot do
        - which tools / skills / providers it has
        - what permissions / autonomy it has
        - which interfaces it exposes
        - which dependencies it carries
    """

    identity: AgentIdentity
    generated_at: datetime = Field(default_factory=utc_now)
    manifest_version: str = "1.0"

    def summary(self) -> dict[str, Any]:
        i = self.identity
        return {
            "agent_id": i.agent_id,
            "name": i.name,
            "version": i.version,
            "seed_version": i.seed_version,
            "runtime_version": i.runtime_version,
            "specialization": i.specialization,
            "capabilities": i.capabilities,
            "permissions": i.permissions,
            "autonomy_level": i.autonomy_level.name,
            "risk_profile": i.risk_profile.value,
            "providers": i.providers,
            "tools": i.tools,
            "skills": i.skills,
            "memory_domains": i.memory_domains,
            "interfaces": i.interfaces,
            "dependencies": i.dependencies,
            "constraints": i.constraints,
            "endpoint": i.endpoint,
        }
