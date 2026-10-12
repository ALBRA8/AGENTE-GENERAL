"""A2A Agent Card — derived from the existing AgentManifest.

The AgentCard is the discovery document an ALBRA agent exposes via the
A2A protocol. It is NEVER a parallel source of truth — every field is
DERIVED from the canonical AgentManifest (single source of truth).

Specialization layers may add extra `skills` via `extra_skills`, but the
identity fields (name, version, endpoint, capabilities) ALWAYS come from
the manifest. This guarantees that the A2A card cannot drift away from
the manifest's view of the agent.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.identity import AgentManifest
from albra_seed.core.types import utc_now


class A2ACardSkill(BaseModel):
    """A single skill advertised on the Agent Card."""

    id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    input_modes: list[str] = Field(default_factory=lambda: ["text"])
    output_modes: list[str] = Field(default_factory=lambda: ["text"])


class AgentCard(BaseModel):
    """A2A protocol Agent Card.

    Field semantics follow the A2A protocol's `AgentCard` schema but are
    populated exclusively by `manifest_to_card`. No business logic, no
    domain knowledge.
    """

    protocol_version: str = "1.0"
    name: str
    description: str = ""
    version: str = "1.0.0"
    endpoint: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    skills: list[A2ACardSkill] = Field(default_factory=list)
    default_input_modes: list[str] = Field(default_factory=lambda: ["text"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text"])
    authentication: dict[str, Any] = Field(default_factory=dict)
    provider: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    generated_at: str = Field(default_factory=lambda: utc_now().isoformat())


def manifest_to_card(
    manifest: AgentManifest,
    *,
    endpoint: str | None = None,
    description: str = "",
    authentication_schemes: list[dict[str, Any]] | None = None,
    extra_skills: list[A2ACardSkill] | None = None,
) -> AgentCard:
    """Translate an AgentManifest into an A2A AgentCard.

    The manifest is the SINGLE SOURCE OF TRUTH. The `endpoint` argument
    MAY override the manifest's identity.endpoint (useful when the card
    is being generated for a specific transport binding).

    `authentication_schemes` and `extra_skills` are specialization
    extensions; both default to empty/None so the seed produces a valid
    card with zero configuration.
    """
    identity = manifest.identity
    # Derive skills from the manifest's identity.skills list. Each skill
    # name becomes an A2ACardSkill with default input/output modes.
    derived_skills: list[A2ACardSkill] = [
        A2ACardSkill(id=s, name=s) for s in identity.skills
    ]
    if extra_skills:
        # Avoid duplicates by id; extra skills take precedence on conflict.
        seen = {s.id for s in derived_skills}
        for extra in extra_skills:
            if extra.id in seen:
                continue
            derived_skills.append(extra)
            seen.add(extra.id)

    # Authentication schemes: empty by default (CORE_ONLY mode = no auth
    # required); when supplied, recorded verbatim.
    auth: dict[str, Any] = {}
    if authentication_schemes:
        auth["schemes"] = list(authentication_schemes)

    # Provider info — derived from identity.providers + the seed/runtime
    # versions for traceability. NO domain knowledge.
    provider_info: dict[str, Any] = {
        "providers": list(identity.providers),
        "seed_version": identity.seed_version,
        "runtime_version": identity.runtime_version,
        "specialization": identity.specialization,
    }

    return AgentCard(
        protocol_version="1.0",
        name=identity.name,
        description=description or f"ALBRA agent: {identity.name}",
        version=identity.version,
        endpoint=endpoint or identity.endpoint,
        capabilities=list(identity.capabilities),
        skills=derived_skills,
        default_input_modes=list(identity.interfaces) or ["text"],
        default_output_modes=["text"],
        authentication=auth,
        provider=provider_info,
        metadata={
            "agent_id": identity.agent_id,
            "autonomy_level": identity.autonomy_level.name,
            "risk_profile": identity.risk_profile.value,
            "manifest_version": manifest.manifest_version,
        },
    )


__all__ = ["A2ACardSkill", "AgentCard", "manifest_to_card"]
