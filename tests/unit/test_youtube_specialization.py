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
        outputs = {"projects": [], "count": 0} if op.endswith("list_projects") \
            else {"status": "ACCEPTED"}
        return httpx.Response(200, json={
            "contract_version": "1.0", "execution_id": body["execution_id"],
            "status": "ACCEPTED", "operation": op,
            "project_id": "aabbccddeeff" if op == "youtube.create" else None,
            "outputs": outputs, "errors": []})
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


# ── skills (§14) ────────────────────────────────────────────────────
_EXPECTED_SKILLS = {
    "skill.youtube.create", "skill.youtube.submit_production",
    "skill.youtube.launch", "skill.youtube.status", "skill.youtube.recover",
    "skill.youtube.qa", "skill.youtube.publish", "skill.youtube.flow_status",
    "skill.youtube.doctor",
}


def test_nueve_skills_registradas_con_validador_real():
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
        "youtube_publish", "youtube_flow_status", "youtube_doctor"}


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
