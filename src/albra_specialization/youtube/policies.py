# ─────────────────────────────────────────────────────────────────────
# ALBRA SPECIALIZATION · youtube/policies.py
# ─────────────────────────────────────────────────────────────────────
# Reglas de política/firewall y grants RBAC RECOMENDADOS para las
# skills de YouTube. Se devuelven como DATOS: el host las adopta en SU
# PolicyEngine/Firewall/PermissionRegistry (el seed no se toca).
#
# Principio: lecturas ALLOW con rate-limit; mutaciones ALLOW con
# permiso; publicación → REQUIRE_HITL (aprobación humana, side effect
# externo); el motor remoto jamás decide solo.
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import Any

from albra_seed.core.types import AutonomyLevel, PolicyDecision, RiskLevel
from albra_seed.firewall.rules import FirewallRule
from albra_seed.policy.rules import PolicyRule

_SKILL_ACTIONS = [
    "skill.youtube.create",
    "skill.youtube.submit_production",
    "skill.youtube.launch",
    "skill.youtube.status",
    "skill.youtube.recover",
    "skill.youtube.qa",
    "skill.youtube.publish",
    "skill.youtube.flow_status",
    "skill.youtube.doctor",
    # P3-B (PB-02): skills read-only justificadas por INT-015
    "skill.youtube.validate",
    "skill.youtube.system_status",
]

_READ_SKILLS = {"skill.youtube.status", "skill.youtube.qa",
                "skill.youtube.flow_status", "skill.youtube.doctor",
                "skill.youtube.validate", "skill.youtube.system_status"}
_HITL_SKILLS = {"skill.youtube.publish"}


def recommended_policy_rules() -> list[PolicyRule]:
    """First-match-wins: lecturas primero, HITL de publicación, resto allow
    con permiso requerido."""
    rules: list[PolicyRule] = []
    for i, action in enumerate(_SKILL_ACTIONS):
        if action in _HITL_SKILLS:
            rules.append(PolicyRule(
                name=f"youtube-hitl-{action.removeprefix('skill.')}",
                description="Publicación a YouTube = efecto externo: "
                            "exige aprobación humana (HITL) y permiso.",
                action_pattern=action,
                risk_threshold=RiskLevel.HIGH,
                autonomy_required=AutonomyLevel.L4_EXECUTE_SENSITIVE,
                required_permission=f"{action}.execute",
                decision=PolicyDecision.REQUIRE_HITL,
                priority=20 - i,
            ))
        elif action in _READ_SKILLS:
            rules.append(PolicyRule(
                name=f"youtube-allow-{action.removeprefix('skill.')}",
                description="Lectura del arnés: permitida con permiso.",
                action_pattern=action,
                risk_threshold=RiskLevel.LOW,
                required_permission=f"{action}.execute",
                decision=PolicyDecision.ALLOW,
                priority=20 - i,
            ))
        else:
            rules.append(PolicyRule(
                name=f"youtube-allow-{action.removeprefix('skill.')}",
                description="Mutación del arnés: permitida con permiso "
                            "(el arnés aplica su propia frontera de "
                            "permisos y riesgo).",
                action_pattern=action,
                risk_threshold=RiskLevel.MEDIUM,
                required_permission=f"{action}.execute",
                decision=PolicyDecision.ALLOW,
                priority=20 - i,
            ))
    return rules


def recommended_firewall_rules() -> list[FirewallRule]:
    """Perímetro del tool externo: allow con rate-limit; el dry-run está
    siempre disponible (is_dry_run_capable en el contrato del tool)."""
    return [
        FirewallRule(
            name="youtube-harness-tool-allow",
            description="Tool youtube_harness_execute: permitido con "
                        "rate-limit moderado (servicio externo).",
            action_pattern="tool.youtube_harness.*",
            risk_threshold=RiskLevel.LOW,
            decision=PolicyDecision.ALLOW,
            rate_limit=60,
            rate_window_seconds=60,
            priority=10,
        ),
    ]


def recommended_grants() -> list[tuple[str, str]]:
    """(rol, permiso) a otorgar en el PermissionRegistry del host."""
    grants: list[tuple[str, str]] = [
        ("operator", "tool.youtube_harness.execute"),
    ]
    for action in _SKILL_ACTIONS:
        grants.append(("operator", f"{action}.execute"))
    return grants


def policy_snapshot() -> dict[str, Any]:
    return {
        "skills": _SKILL_ACTIONS,
        "hitl": sorted(_HITL_SKILLS),
        "read_only": sorted(_READ_SKILLS),
        "tool": "youtube_harness_execute",
        "note": "El arnés aplica su propia frontera (permisos youtube.* y "
                "destructivas con youtube.destroy); el host añade HITL "
                "para publicación y rate-limit para el tool externo.",
    }
