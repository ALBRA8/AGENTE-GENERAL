# ─────────────────────────────────────────────────────────────────────
# ALBRA SPECIALIZATION · youtube/ — especialización YouTube del host
# ─────────────────────────────────────────────────────────────────────
# AGENTE-GENERAL consume el arnés ALBRA8/YOUTUBE-AUTOMATION vía Harness
# Contract V1 (/api/harness/v1). Este paquete es la capa de COORDINACIÓN
# del host: tools + skills + políticas. CERO lógica del motor: el motor
# vive en el arnés (separación host/puesto del mandato).
# ─────────────────────────────────────────────────────────────────────
from .client import (YoutubeHarnessClient, HarnessConfigError,
                     HarnessTransportError, HarnessProtocolError)
from .tools import YoutubeHarnessTool, TOOL_NAME
from .skills import build_youtube_skills
from . import policies

__all__ = ["YoutubeHarnessClient", "HarnessConfigError",
           "HarnessTransportError", "HarnessProtocolError",
           "YoutubeHarnessTool", "TOOL_NAME", "build_youtube_skills",
           "policies", "install_youtube_harness"]


def install_youtube_harness(runtime, *, client: YoutubeHarnessClient | None = None,
                            base_url: str | None = None,
                            api_key: str | None = None) -> dict:
    """Enchufa la especialización YouTube en un Runtime ya iniciado.

    Orden conforme al seed (post-startup, sin modificar albra_seed):
      tool_registry → runtime.register_tool → skill_registry → capability.
    Devuelve un snapshot de lo registrado (para audit/doctor del host).

    Si el arnés no está configurado (sin YOUTUBE_HARNESS_URL), las
    piezas se registran IGUAL: el tool responde "no configurado" y las
    skills propagan el error — nunca un silencio.
    """
    from albra_seed.capabilities.registry import CapabilityRegistry
    from albra_seed.capabilities.resolver import CapabilityResolver
    from albra_seed.core.base import Capability
    from albra_seed.core.types import RiskLevel
    from albra_seed.skills.registry import SkillRegistry
    from albra_seed.skills.validator import SkillValidator
    from albra_seed.tools.registry import ToolRegistry

    c = client or YoutubeHarnessClient(base_url=base_url, api_key=api_key)
    tool = YoutubeHarnessTool(client=c)

    # 1) tool registry (componente existente o nuevo)
    tool_registry = runtime.get_component("tool_registry")
    if tool_registry is None:
        tool_registry = ToolRegistry()
        runtime.register("tool_registry", tool_registry)
    tool_registry.register(tool)
    runtime.register_tool(tool)

    # 2) skill registry con validador que conoce los tools reales
    skill_registry = runtime.get_component("skill_registry")
    if skill_registry is None:
        skill_registry = SkillRegistry(
            validator=SkillValidator(known_tool_ids=set(tool_registry.list())))
        runtime.register("skill_registry", skill_registry)
    registered_skills: list[str] = []
    for contract, handler in build_youtube_skills(c):
        skill_registry.register(contract, handler)
        registered_skills.append(contract.skill_id)

    # 3) capability externa declarada
    cap_registry = runtime.get_component("capability_registry")
    if cap_registry is None:
        cap_registry = CapabilityRegistry()
        runtime.register("capability_registry", cap_registry)
    capability = Capability(
        capability_id="youtube_harness",
        name="YouTube Automation Harness",
        description="Producción audiovisual (YouTube/shorts) vía el arnés "
                    "ALBRA8/YOUTUBE-AUTOMATION con Harness Contract V1.",
        version="1.0.0",
        requires_tools=[TOOL_NAME],
        requires_permissions=["skill.youtube.status.execute"],
        risk_level=RiskLevel.MEDIUM,
        is_external=True,
    )
    cap_registry.register(capability, enabled=True)
    resolver = CapabilityResolver(cap_registry)

    return {
        "tool": TOOL_NAME,
        "skills": registered_skills,
        "capability": capability.capability_id,
        "harness_configured": c.is_configured(),
        "policy_snapshot": policies.policy_snapshot(),
        "resolver": resolver,
    }
