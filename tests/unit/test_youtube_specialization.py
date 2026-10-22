"""Batería · especialización YouTube del host (skill.youtube.*).

Cubre el mandato §14 (skills de coordinación, cero motor) y §15
(host-agnostic: el host solo habla Harness Contract V1 por HTTP):
· cliente con httpx.MockTransport (patrón a2a/transport.py)
· tool externo: execute + dry-run sin efectos + no configurado
· 9 skills registradas en SkillRegistry con validador real
· permisos HIGH sin permissions ⇒ inválido (convención del seed)
· HITL/policy snapshot; limpieza de entornos (sin side effects)
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest

from albra_seed.skills.registry import SkillRegistry
from albra_seed.skills.validator import SkillValidator
from albra_seed.core.types import RiskLevel

from albra_specialization.youtube import (
    YoutubeHarnessClient, YoutubeHarnessTool, TOOL_NAME,
    build_youtube_skills, policies)
from albra_specialization.youtube.client import HarnessConfigError

# ── mock del arnés (contrato V1) ────────────────────────────────────
_HARNESS_URL = "https://harness.test"


def _mock_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/harness/v1/manifest":
        auth = request.headers.get("Authorization", "")
        if not auth:
            return httpx.Response(401, json={"detail": "clave maestra requerida"})
        return httpx.Response(200, json={
            "harness": "youtube-harness", "contract_version": "1.0",
            "operations": [{"name": "youtube.list_projects"}]})
    if request.url.path == "/api/harness/v1/execute":
        auth = request.headers.get("Authorization", "")
        if not auth:
            return httpx.Response(401, json={"detail": "clave maestra requerida"})
        body = json.loads(request.content)
        op = body["operation"]
        if op == "youtube.delete" and "youtube.destroy" not in body.get("permissions", []):
            return httpx.Response(200, json={
                "contract_version": "1.0", "execution_id": body["execution_id"],
                "status": "BLOCKED", "operation": op,
                "errors": [{"code": "PERMISSION_MISSING", "message": "youtube.destroy"}]})
        # P3-B (PB-02): respuestas de contrato para las skills nuevas
        if op in ("youtube.validate_production", "youtube.system_status"):
            # doble fiel del arnés: sin youtube.read declarado → BLOCKED
            if "youtube.read" not in body.get("permissions", []):
                return httpx.Response(200, json={
                    "contract_version": "1.0",
                    "execution_id": body["execution_id"], "operation": op,
                    "status": "BLOCKED",
                    "errors": [{"code": "PERMISSION_MISSING",
                                "message": "youtube.read"}]})
        if op == "youtube.validate_production":
            pj = (body.get("input") or {}).get("production_json") or {}
            if isinstance(pj.get("escenas"), list) and pj.get("titulo"):
                envelope = {"status": "ACCEPTED",
                            "outputs": {"valid": True, "outcome": "OK",
                                        "errors": [], "warnings": [],
                                        "normalizations": [], "unidades": 1}}
            else:
                envelope = {"status": "BLOCKED",
                            "outputs": {"valid": False,
                                        "outcome": "VALIDATION_ERROR",
                                        "errors": [{"code": "VALIDATION",
                                                    "message": "payload incompleto"}],
                                        "warnings": [], "normalizations": []}}
        elif op == "youtube.system_status":
            envelope = {"status": "ACCEPTED", "outputs": {
                "harness": "youtube-harness",
                "providers": {"nvidia": False, "gemini": False,
                              "whisper": False, "youtube_publish": False},
                "counters": {"ACCEPTED": 2}}}
        elif op == "youtube.publish":
            pid = (body.get("input") or {}).get("project_id") or ""
            if pid.startswith("fail"):
                envelope = {"status": "FAILED",
                            "errors": [{"code": "PROVIDER_FAILURE",
                                        "message": "publish falló (fixture)"}],
                            "recovery": {"recoverable": True,
                                         "hint": "youtube.recover o youtube.production_doctor"}}
            else:
                # G-01 corregido: el éxito viaja como PUBLISHED (estado §9)
                envelope = {"status": "PUBLISHED",
                            "outputs": {"youtube_id": "vidg01ok1",
                                        "url": "https://youtube.com/watch?v=vidg01ok1",
                                        "external_status": "PUBLISHED"}}
        else:
            outputs = {"projects": [], "count": 0} if op.endswith("list_projects") \
                else {"status": "ACCEPTED"}
            envelope = {"status": "ACCEPTED", "outputs": outputs}
        resp = {"contract_version": "1.0", "execution_id": body["execution_id"],
                "operation": op,
                "project_id": "aabbccddeeff" if op == "youtube.create" else None,
                "errors": [], "warnings": [], "recovery": {}}
        resp.update(envelope)
        return httpx.Response(200, json=resp)
    return httpx.Response(404, json={"detail": "not found"})


def _client() -> YoutubeHarnessClient:
    return YoutubeHarnessClient(
        base_url=_HARNESS_URL, api_key="test-key",
        transport=httpx.MockTransport(_mock_handler))


# ── cliente ─────────────────────────────────────────────────────────
def test_manifest_requiere_y_usa_credencial():
    c = _client()
    m = c.manifest()
    assert m["harness"] == "youtube-harness"
    assert m["contract_version"] == "1.0"


def test_execute_devuelve_contrato_tal_cual():
    c = _client()
    r = c.execute("youtube.list_projects", permissions=["youtube.read"])
    assert r["status"] == "ACCEPTED" and r["operation"] == "youtube.list_projects"
    assert "outputs" in r and "errors" in r


def test_sin_url_lanza_config_error():
    c = YoutubeHarnessClient(api_key="x")  # sin base_url ni env
    assert not c.is_configured()
    with pytest.raises(HarnessConfigError):
        c.execute("youtube.list_projects")


def test_bloqueado_por_permisos_se_propaga_como_contrato():
    c = _client()
    r = c.execute("youtube.delete", input={"project_id": "aabbccddeeff"},
                  permissions=["youtube.read"])
    assert r["status"] == "BLOCKED"
    assert r["errors"][0]["code"] == "PERMISSION_MISSING"


# ── tool externo ────────────────────────────────────────────────────
def test_tool_execute_ok():
    tool = YoutubeHarnessTool(client=_client())
    res = asyncio.run(tool.execute(
        {"operation": "youtube.list_projects",
         "permissions": ["youtube.read"]}))
    assert res.success is True
    assert res.output["operation"] == "youtube.list_projects"


def test_tool_dry_run_sin_efectos():
    tool = YoutubeHarnessTool(client=_client())
    res = asyncio.run(tool.execute(
        {"operation": "youtube.delete",
         "input": {"project_id": "aabbccddeeff"},
         "permissions": ["youtube.destroy"]}, dry_run=True))
    assert res.success is True
    assert res.output["dry_run"] is True
    assert res.output["would_execute"] == "youtube.delete"
    assert "status" not in res.output  # nada se ejecutó


def test_tool_operation_requerida():
    tool = YoutubeHarnessTool(client=_client())
    res = asyncio.run(tool.execute({}))
    assert res.success is False and "operation" in (res.error or "")


# ── skills (§14 + PB-02 de INT-015) ──────────────────────────────
_EXPECTED_SKILLS = {
    "skill.youtube.create", "skill.youtube.submit_production",
    "skill.youtube.launch", "skill.youtube.status", "skill.youtube.recover",
    "skill.youtube.qa", "skill.youtube.publish", "skill.youtube.flow_status",
    "skill.youtube.doctor", "skill.youtube.validate",
    "skill.youtube.system_status",
}


def test_once_skills_registradas_con_validador_real():
    reg = SkillRegistry(validator=SkillValidator(
        known_tool_ids={TOOL_NAME}))
    specs = build_youtube_skills(_client())
    for contract, handler in specs:
        resultado = reg.register(contract, handler)
        assert resultado.valid is True, getattr(resultado, "errors", None)
    assert {c.skill_id for c, _ in specs} == _EXPECTED_SKILLS
    assert set(reg.list()) == {
        "youtube_create", "youtube_submit_production", "youtube_launch",
        "youtube_status", "youtube_recover", "youtube_qa",
        "youtube_publish", "youtube_flow_status", "youtube_doctor",
        "youtube_validate", "youtube_system_status"}


def test_skill_high_sin_permisos_es_invalida():
    # convención del seed: HIGH/CRITICAL sin permissions ⇒ valid=False
    from albra_seed.skills.validator import SkillValidator as V
    from albra_seed.skills.contract import SkillContract
    from albra_seed.core.types import RiskLevel
    bad = SkillContract(
        skill_id="skill.youtube.probesinpermiso", name="probe",
        version="1.0.0", objective="probar validador",
        input_schema={"type": "object", "properties": {}},
        risk_level=RiskLevel.HIGH, permissions=[],
        dependencies=[TOOL_NAME])
    res = V(known_tool_ids={TOOL_NAME}).validate(bad)
    assert res.valid is False


def test_skill_status_delega_en_arnes():
    reg = SkillRegistry(validator=SkillValidator(known_tool_ids={TOOL_NAME}))
    for contract, handler in build_youtube_skills(_client()):
        reg.register(contract, handler)
    res = asyncio.run(reg.execute(
        "skill.youtube.status", {"project_id": "aabbccddeeff"},
        caller_permissions={"skill.youtube.status.execute"}))
    assert res.success is True
    assert res.output["operation"] == "youtube.project_status"
    assert res.output["harness_status"] == "ACCEPTED"


def test_skill_create_genera_project_id():
    reg = SkillRegistry(validator=SkillValidator(known_tool_ids={TOOL_NAME}))
    for contract, handler in build_youtube_skills(_client()):
        reg.register(contract, handler)
    res = asyncio.run(reg.execute(
        "skill.youtube.create", {"idea": "video de prueba"},
        caller_permissions={"skill.youtube.create.execute"}))
    assert res.output["project_id"] == "aabbccddeeff"


def test_skills_exigen_permiso_del_caller():
    reg = SkillRegistry(validator=SkillValidator(known_tool_ids={TOOL_NAME}))
    for contract, handler in build_youtube_skills(_client()):
        reg.register(contract, handler)
    res = asyncio.run(reg.execute(
        "skill.youtube.create", {"idea": "x"},
        caller_permissions=set()))  # sin el permiso de la skill
    assert res.success is False
    assert "permission" in str(res.error).lower()


# ── políticas recomendadas ──────────────────────────────────────────
def test_politicas_publish_require_hitl():
    rules = policies.recommended_policy_rules()
    publish = [r for r in rules if r.action_pattern == "skill.youtube.publish"]
    assert publish and str(publish[0].decision.value) in (
        "require_hitl", "REQUIRE_HITL")
    reads = [r for r in rules if r.action_pattern == "skill.youtube.status"]
    assert reads and str(reads[0].decision.value) in ("allow", "ALLOW")


def test_firewall_rate_limit_del_tool():
    fw = policies.recommended_firewall_rules()
    assert any(r.action_pattern == "tool.youtube_harness.*"
               and r.rate_limit > 0 for r in fw)


def test_policy_snapshot_sin_secretos():
    snap = json.dumps(policies.policy_snapshot())
    assert "api_key" not in snap.lower() and "secret" not in snap.lower()


# ── P3-B (PB-02): skills justificadas por INT-015 ───────────────────
def _registry_con_todas() -> SkillRegistry:
    reg = SkillRegistry(validator=SkillValidator(known_tool_ids={TOOL_NAME}))
    for contract, handler in build_youtube_skills(_client()):
        reg.register(contract, handler)
    return reg


def test_skill_validate_distingue_payload_valido_de_invalido():
    reg = _registry_con_todas()
    valido = {"production_json": {"titulo": "Video de prueba",
                                  "escenas": [{"id": 1, "texto": "hola"}]}}
    res = asyncio.run(reg.execute(
        "skill.youtube.validate", valido,
        caller_permissions={"skill.youtube.validate.execute"}))
    assert res.success is True
    assert res.output["operation"] == "youtube.validate_production"
    assert res.output["harness_status"] == "ACCEPTED"
    assert res.output["outputs"]["valid"] is True
    # negativo: payload roto → BLOCKED propagado, NADA se crea
    res_bad = asyncio.run(reg.execute(
        "skill.youtube.validate", {"production_json": {"x": 1}},
        caller_permissions={"skill.youtube.validate.execute"}))
    assert res_bad.success is True  # el envelope viaja, no explota
    assert res_bad.output["harness_status"] == "BLOCKED"
    assert res_bad.output["outputs"]["valid"] is False
    assert res_bad.output["outputs"]["errors"][0]["code"] == "VALIDATION"


def test_skill_validate_exige_production_json():
    reg = _registry_con_todas()
    res = asyncio.run(reg.execute(
        "skill.youtube.validate", {},
        caller_permissions={"skill.youtube.validate.execute"}))
    # el input_schema de la skill (required: production_json) la rechaza
    # ANTES de salir hacia el arnés — nada se envía, nada se crea
    assert res.success is False
    assert "production_json" in str(res.error).lower()


def test_skill_system_status_expone_salud_del_arnes():
    reg = _registry_con_todas()
    res = asyncio.run(reg.execute(
        "skill.youtube.system_status", {},
        caller_permissions={"skill.youtube.system_status.execute"}))
    assert res.success is True
    assert res.output["operation"] == "youtube.system_status"
    assert res.output["harness_status"] == "ACCEPTED"
    providers = res.output["outputs"].get("providers") or {}
    assert set(providers) == {"nvidia", "gemini", "whisper",
                              "youtube_publish"}
    assert "counters" in res.output["outputs"]


def test_skills_pb02_riesgo_permisos_y_politicas_coherentes():
    specs = {c.skill_id: c for c, _ in build_youtube_skills(_client())}
    for sid in ("skill.youtube.validate", "skill.youtube.system_status"):
        c = specs[sid]
        # riesgo coherente con la operación real (ops LOW de lectura)
        assert c.risk_level == RiskLevel.LOW
        assert c.is_idempotent is True
        assert c.evidence_required is False
        # convención del seed: permiso skill.<nombre>.execute
        assert c.permissions == [f"{sid}.execute"]
        # el permiso del arnés (youtube.read) se verifica FUNCIONALMENTE
        # en test_skills_pb02_ejercen_youtube_read_ante_el_arnes (el
        # SkillContract del seed no persiste metadata arbitraria: hallazgo
        # G-13 del informe P3-B, fuera del alcance de esta misión)
    # políticas: ALLOW de lectura con permiso; HITL sigue solo en publish
    rules = {r.action_pattern: r for r in policies.recommended_policy_rules()}
    for sid in ("skill.youtube.validate", "skill.youtube.system_status"):
        assert rules[sid].decision.value in ("allow", "ALLOW")
        assert rules[sid].required_permission == f"{sid}.execute"
    assert str(rules["skill.youtube.publish"].decision.value) in (
        "require_hitl", "REQUIRE_HITL")
    # grants RBAC y snapshot declarativo
    g = {p for _role, p in policies.recommended_grants()}
    assert {"skill.youtube.validate.execute",
            "skill.youtube.system_status.execute"} <= g
    snap = policies.policy_snapshot()
    assert {"skill.youtube.validate",
            "skill.youtube.system_status"} <= set(snap["read_only"])
    assert snap["hitl"] == ["skill.youtube.publish"]


def test_skills_pb02_ejercen_youtube_read_ante_el_arnes():
    """Permiso del arnés ejercido REALMENTE en el cuerpo HTTP: el doble
    del contrato responde BLOCKED PERMISSION_MISSING si la skill no
    declara youtube.read (auditoría funcional, no declarativa)."""
    reg = _registry_con_todas()
    for sid, args in (
        ("skill.youtube.validate",
         {"production_json": {"titulo": "t", "escenas": []}}),
        ("skill.youtube.system_status", {}),
    ):
        res = asyncio.run(reg.execute(
            sid, args,
            caller_permissions={f"{sid}.execute"}))
        assert res.success is True
        assert res.output["harness_status"] == "ACCEPTED", (
            f"{sid} no declaró youtube.read: {res.output}")


def test_skill_publish_exito_propaga_published_g01():
    """G-01 corregido: el éxito del publish viaja PUBLISHED por todas
    las capas del host (client → skill), sin errors[] discrepantes."""
    reg = _registry_con_todas()
    res = asyncio.run(reg.execute(
        "skill.youtube.publish", {"project_id": "aabbccddeeff"},
        caller_permissions={"skill.youtube.publish.execute"}))
    assert res.success is True
    assert res.output["harness_status"] == "PUBLISHED"
    assert res.output["errors"] == []
    assert res.output["outputs"]["youtube_id"] == "vidg01ok1"
    assert res.output["outputs"]["external_status"] == "PUBLISHED"


def test_skill_publish_fallo_autentico_propaga_errores():
    """Un fallo real del proveedor sigue siendo FAILED + errors[] +
    recovery — la corrección G-01 no oculta errores auténticos."""
    reg = _registry_con_todas()
    res = asyncio.run(reg.execute(
        "skill.youtube.publish", {"project_id": "failaabbccddeeff"},
        caller_permissions={"skill.youtube.publish.execute"}))
    assert res.success is True  # el envelope del contrato viaja tal cual
    assert res.output["harness_status"] == "FAILED"
    assert res.output["errors"][0]["code"] == "PROVIDER_FAILURE"
    assert res.output["recovery"].get("recoverable") is True
    assert "youtube_id" not in res.output["outputs"]
