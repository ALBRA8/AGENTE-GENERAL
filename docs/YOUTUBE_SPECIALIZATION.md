# Especialización YouTube de AGENTE-GENERAL — consumidor del arnés

**Repo:** ALBRA8/AGENTE-GENERAL · **Base:** `9b28990` · **Rama:** `feature/youtube-harness`
**Rol:** PRIMER HOST oficial del arnés `ALBRA8/YOUTUBE-AUTOMATION` (Harness Contract V1, `/api/harness/v1`).

## 1. Qué es (y qué NO es)

Este paquete (`src/albra_specialization/youtube/`) es la **capa de coordinación** del host:
skills `skill.youtube.*` + tool externo + políticas recomendadas. **NO contiene ninguna
lógica del motor audiovisual** (mandato §14): todo efecto real lo ejecuta el arnés vía
`POST /api/harness/v1/execute` y el host solo interpreta `HarnessResult` (estados §9 ya
normalizados por el arnés).

```text
AGENTE-GENERAL (host)
  skill.youtube.* (coordinación)  →  youtube_harness_execute (tool externo, httpx+Bearer)
                                          │  Harness Contract V1
                                          ▼
                              YOUTUBE-AUTOMATION /api/harness/v1  (arnés)
                                          ▼
                              EXISTING ENGINE (pipeline/Flow/HANDS)
```

## 2. Clasificación (§3, lado host)

| Componente | Clase | Nota |
|---|---|---|
| `src/albra_seed/` | DO_NOT_TOUCH | seed intocado (paridad + contaminación: PASS) |
| `src/albra_specialization/youtube/client.py` | ADAPT_GENERAL | cliente m2m (httpx lazy, Bearer, errores tipados, MockTransport) |
| `src/albra_specialization/youtube/tools.py` | ADAPT_GENERAL | `youtube_harness_execute` is_external, dry-run capable |
| `src/albra_specialization/youtube/skills.py` | ADAPT_GENERAL | 9 skills; handlers por closure; permisos skill ↔ arnés separados |
| `src/albra_specialization/youtube/policies.py` | ADAPT_GENERAL | reglas recomendadas como DATOS (el host decide) |
| `tests/unit/test_youtube_specialization.py` | ADAPT_GENERAL | 15 checks con arnés simulado |

## 3. Skills (§14)

`skill.youtube.create · submit_production · launch · status · recover · qa · publish · flow_status · doctor`

- Convención del seed: `skill_id="skill.<nombre>"`, permiso `skill.<nombre>.execute`
  (el SkillExecutor lo contrasta con el caller); los permisos del ARNÉS (`youtube.*`)
  los ejerce el handler y viajan en `contract.metadata.harness_permissions`.
- Riesgo: lecturas LOW · create/submit/recover MEDIUM · launch/publish HIGH
  (`evidence_required=True`). El arnés, además, exige sus propios permisos
  (`youtube.destroy` para destructivas): doble frontera host+arnés.

## 4. Políticas recomendadas (no invasivas)

`policies.recommended_policy_rules()`: lecturas ALLOW, mutaciones ALLOW con permiso,
**`skill.youtube.publish` → REQUIRE_HITL** (side effect externo ⇒ aprobación humana;
autonomía L4_EXECUTE_SENSITIVE). `recommended_firewall_rules()`: tool externo con
rate-limit 60/min. `recommended_grants()`: (rol `operator`, permisos). Se entregan
como datos: el host los adopta en SU PolicyEngine/Firewall/RBAC (el seed no se toca).

## 5. Configuración (§18: sin secretos hardcodeados)

```bash
export YOUTUBE_HARNESS_URL="http://127.0.0.1:8000"   # dónde vive el arnés
export YOUTUBE_HARNESS_API_KEY="..."                 # = MASTER_API_KEY del arnés
```

La clave NUNCA se loguea y `SecretsManager` la auto-redacta en snapshots (regex
`api[_-]?key`). Sin URL configurada, las piezas se registran igual y el tool responde
"harness no configurado" — nunca un silencio.

## 6. Instalación en el runtime

```python
runtime = Runtime(config=SeedConfig())
await runtime.startup()
# ... wiring del host (firewall, policy, providers, identidad) ...
from albra_specialization.youtube import install_youtube_harness
resumen = install_youtube_harness(runtime)
# → {"tool": "youtube_harness_execute", "skills": [...9...],
#    "capability": "youtube_harness", "harness_configured": bool,
#    "policy_snapshot": {...}}
```

Orden conforme al seed: post-`startup()`, `tool_registry` → `register_tool` →
`skill_registry` (validador con `known_tool_ids` reales) → `capability_registry`
(`youtube_harness`, `is_external=True`).

## 7. Agnosticidad (§15, lado host)

El host NO importa nada del arnés (solo HTTP+contrato); el arnés NO importa nada del
host (verificado en YOUTUBE-AUTOMATION). Cualquier otro host (Hermes, OpenClaw, …)
reproduce la misma integración hablando Harness Contract V1: ver
`/api/harness/v1/manifest?knowledge=1` del arnés.

## 8. Verificación

- `pytest tests/unit/test_youtube_specialization.py` → **15 passed** (arnés simulado con
  `httpx.MockTransport`: credencial, dry-run sin efectos, permisos BLOCKED propagados,
  9 skills con validador real, HITL de publicación, rate-limit, sin secretos).
- Regresión del seed: `tests/unit + tests/contracts` → **mismos 18 fallos preexistentes
  del entorno** (subprocess/render_app sin `src/` en path; verificados idénticos en árbol
  limpio con stash) y **450→465 passed** (+15 propios, 0 nuevas roturas).
- `scripts/contamination_scan.py` → **PASS** (cero contaminación de dominio en el seed).
