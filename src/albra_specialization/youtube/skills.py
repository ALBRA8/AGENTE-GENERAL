# ─────────────────────────────────────────────────────────────────────
# ALBRA SPECIALIZATION · youtube/skills.py
# ─────────────────────────────────────────────────────────────────────
# Skills skill.youtube.* del host (mandato §14): capa de COORDINACIÓN.
# NINGUNA skill contiene lógica del motor: todas delegan en el tool
# youtube_harness_execute (que habla Harness Contract V1 con el arnés).
#
# Convención del seed (docs/SPECIALIZATION.md):
#   skill_id = "skill.<nombre>" · permiso = "skill.<nombre>.execute"
#   HIGH/CRITICAL sin permissions ⇒ inválido (SkillValidator)
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from albra_seed.core.types import RiskLevel
from albra_seed.skills.contract import SkillContract

from .client import YoutubeHarnessClient
from .tools import TOOL_NAME

SkillHandler = Callable[[dict[str, Any], Any], Awaitable[dict[str, Any]]]

# permisos del arnés que cada skill ejerce en el contrato (§13)
_P_READ = ["youtube.read"]
_P_CREATE = ["youtube.production.create"]
_P_LAUNCH = ["youtube.production.launch"]
_P_PUBLISH = ["youtube.publish"]
_P_DESTROY = ["youtube.destroy"]


def _object_schema(required: list[str], properties: dict[str, Any]) -> dict:
    return {"type": "object", "required": required,
            "properties": properties}


def _mk_contract(skill_id: str, objective: str, *, risk: RiskLevel,
                 harness_permissions: list[str],
                 input_schema: dict, output_schema: dict | None = None,
                 idempotent: bool = False,
                 max_duration: int = 200) -> SkillContract:
    name = skill_id.removeprefix("skill.").replace(".", "_")
    return SkillContract(
        skill_id=skill_id,
        name=name,
        version="1.0.0",
        objective=objective,
        input_schema=input_schema,
        output_schema=output_schema or {"type": "object"},
        # Convención del seed: el permiso de la SKILL es skill.<nombre>.execute
        # (el SkillExecutor lo contrasta con los permisos del caller). Los
        # permisos del ARNÉS (youtube.*) los ejerce el handler al llamar
        # al contrato — van en metadata para auditoría.
        permissions=[f"{skill_id}.execute"],
        metadata={"harness_permissions": list(harness_permissions),
                  "tool": TOOL_NAME},
        dependencies=[TOOL_NAME],
        risk_level=risk,
        evidence_required=risk in (RiskLevel.HIGH, RiskLevel.CRITICAL),
        acceptance_criteria=[
            "La respuesta refleja el HarnessResult del arnés sin "
            "reinterpretar el negocio",
            "Errores del arnés se propagan con su código de contrato",
        ],
        tests=[f"test_youtube_{name}"],
        max_duration_seconds=max_duration,
        is_idempotent=idempotent,
    )


def _handler(client: YoutubeHarnessClient, operation: str,
             permissions: list[str]) -> SkillHandler:
    """Fábrica de handlers: delegan en el cliente del arnés.

    Por closure (el RunContext del seed no expone el runtime): la skill
    es coordinación pura; la política/HITL del host se aplica en la
    capa de tool/ejecución del propio host.
    """

    async def handler(arguments: dict[str, Any],
                      context: Any = None) -> dict[str, Any]:
        arguments = dict(arguments or {})
        # coordenación compuesta: status con project_id o listado
        op_input = arguments.get("input")
        if not isinstance(op_input, dict):
            op_input = {k: v for k, v in arguments.items()
                        if k not in ("operation", "permissions",
                                     "constraints", "execution_id")}
        result = client.execute(
            operation,
            input=op_input,
            permissions=list(permissions),
            constraints=arguments.get("constraints") or None,
            execution_id=arguments.get("execution_id") or None,
            correlation_id=getattr(context, "correlation_id", None)
            if context is not None else None,
        )
        return {
            "harness_status": result.get("status"),
            "operation": result.get("operation") or operation,
            "project_id": result.get("project_id"),
            "job_id": result.get("job_id"),
            "outputs": result.get("outputs") or {},
            "warnings": result.get("warnings") or [],
            "errors": result.get("errors") or [],
            "recovery": result.get("recovery") or {},
            "execution_id": result.get("execution_id"),
        }

    return handler


# ── definición de las 9 skills (mandato §14) ────────────────────────
def build_youtube_skills(client: YoutubeHarnessClient
                         ) -> list[tuple[SkillContract, SkillHandler]]:
    """Devuelve [(contrato, handler)] para registrar en SkillRegistry."""
    specs = [
        ("skill.youtube.create", RiskLevel.MEDIUM, _P_CREATE,
         "Crea un proyecto de video en el arnés YouTube (sin lanzarlo).",
         _object_schema(["idea"], {
             "idea": {"type": "string"},
             "nicho": {"type": "string"},
             "format": {"type": "string"},
             "style": {"type": "string"},
             "avatar": {"type": "string"},
             "platforms": {"type": "array", "items": {"type": "string"}},
         }), False,
         "youtube.create"),
        ("skill.youtube.submit_production", RiskLevel.MEDIUM, _P_CREATE,
         "Ingiere un Creative Production JSON en el arnés (opcionalmente "
         "lo lanza con launch=true).",
         _object_schema(["production_json"], {
             "production_json": {"type": "object"},
             "launch": {"type": "boolean"},
         }), False,
         "youtube.submit_production"),
        ("skill.youtube.launch", RiskLevel.HIGH, _P_LAUNCH,
         "Lanza el pipeline de producción de un proyecto existente.",
         _object_schema(["project_id"], {"project_id": {"type": "string"}}),
         False, "youtube.launch"),
        ("skill.youtube.status", RiskLevel.LOW, _P_READ,
         "Consulta el estado de un proyecto (o lista proyectos) con "
         "estados normalizados del contrato.",
         _object_schema([], {"project_id": {"type": "string"},
                             "limit": {"type": "integer"}}), True,
         None),  # doble operación: se decide en el handler
        ("skill.youtube.recover", RiskLevel.MEDIUM, _P_LAUNCH,
         "Recupera leases Flow vencidas en el arnés (anti-zombie).",
         _object_schema([], {}), True, "youtube.recover"),
        ("skill.youtube.qa", RiskLevel.LOW, _P_READ,
         "Ejecuta el QA forense del video final de un proyecto.",
         _object_schema(["project_id"], {"project_id": {"type": "string"}}),
         True, "youtube.video_qa"),
        ("skill.youtube.publish", RiskLevel.HIGH, _P_PUBLISH,
         "Publica el video terminado en YouTube (side effect externo, "
         "idempotente, con QA gate). Requiere aprobación humana (HITL).",
         _object_schema(["project_id"], {
             "project_id": {"type": "string"},
             "title": {"type": "string"},
             "description": {"type": "string"},
             "tags": {"type": "array", "items": {"type": "string"}},
             "private": {"type": "boolean"},
         }), False, "youtube.publish"),
        ("skill.youtube.flow_status", RiskLevel.LOW, _P_READ,
         "Estado de la cola Google Flow de un proyecto (jobs/leases).",
         _object_schema(["project_id"], {"project_id": {"type": "string"}}),
         True, "youtube.flow_status"),
        ("skill.youtube.doctor", RiskLevel.LOW, _P_READ,
         "Diagnóstico del arnés: REAL FLOW PREFLIGHT y production_doctor "
         "(audit/fix/verify/report).",
         _object_schema([], {"action": {"type": "string"},
                             "project_id": {"type": "string"},
                             "deep": {"type": "boolean"}}), True,
         None),  # preflight vs doctor según argumentos
    ]

    out: list[tuple[SkillContract, SkillHandler]] = []
    for skill_id, risk, perms, objective, schema, idem, op in specs:
        if op is None:
            handler = _composite_handler(client)
        else:
            handler = _handler(client, op, perms)
        out.append((_mk_contract(skill_id, objective, risk=risk,
                                 harness_permissions=perms,
                                 input_schema=schema,
                                 idempotent=idem), handler))
    return out


def _composite_handler(client: YoutubeHarnessClient) -> SkillHandler:
    """Handlers con doble operación: status (lista o proyecto) y doctor
    (preflight o audit/fix/verify/report)."""

    async def handler(arguments: dict[str, Any],
                      context: Any = None) -> dict[str, Any]:
        arguments = dict(arguments or {})
        return _dispatch_composite(client, arguments, context)

    return handler


def _dispatch_composite(client: YoutubeHarnessClient,
                        arguments: dict[str, Any],
                        context: Any) -> dict[str, Any]:
    pid = arguments.get("project_id")
    if arguments.get("_kind") == "doctor" or "action" in arguments:
        action = str(arguments.get("action") or "audit").lower()
        if pid and action in ("", "preflight"):
            op, perms = "youtube.flow_preflight", _P_READ
            inp = {"project_id": pid}
        else:
            op, perms = "youtube.production_doctor", _P_READ
            inp = {"action": action or "audit",
                   "deep": bool(arguments.get("deep"))}
            if pid:
                inp["project_id"] = pid
        return _wrap(client.execute(op, input=inp, permissions=perms,
                                    correlation_id=getattr(
                                        context, "correlation_id", None)))
    # skill.youtube.status
    if pid:
        return _wrap(client.execute("youtube.project_status",
                                    input={"project_id": pid},
                                    permissions=_P_READ,
                                    correlation_id=getattr(
                                        context, "correlation_id", None)))
    return _wrap(client.execute("youtube.list_projects",
                                input={"limit": arguments.get("limit", 40)},
                                permissions=_P_READ,
                                correlation_id=getattr(
                                    context, "correlation_id", None)))


def _wrap(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "harness_status": result.get("status"),
        "operation": result.get("operation"),
        "project_id": result.get("project_id"),
        "job_id": result.get("job_id"),
        "outputs": result.get("outputs") or {},
        "warnings": result.get("warnings") or [],
        "errors": result.get("errors") or [],
        "recovery": result.get("recovery") or {},
        "execution_id": result.get("execution_id"),
    }
