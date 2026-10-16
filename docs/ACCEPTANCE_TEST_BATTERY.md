# Frente 2 — Batería de pruebas de aceptación de AGENTE-GENERAL

**Estado del documento:** batería definida; ejecución pendiente.  
**Base de trabajo:** rama `fix/windows-compat-grep-sqlite`, commit de referencia `dd2c2b337dd17c044d46a1f9bc9700243e88533c`.  
**Rama de documentación:** `front-2-acceptance-battery`.  
**Regla:** ningún caso se declara APROBADO sin salida de ejecución o evidencia verificable. Los resultados narrados en informes previos se consideran antecedentes hasta poder reproducirlos.

## 1. Estados y registro por ejecución

- **PENDIENTE:** aún no ejecutado o evidencia no revisada.
- **APROBADO:** procedimiento ejecutado y resultado esperado demostrado.
- **FALLIDO:** procedimiento ejecutado y resultado contrario al esperado.
- **BLOQUEADO:** no puede ejecutarse por entorno, permisos, dependencia o acceso.
- Una prueba omitida no equivale a una aprobada. Registrar fecha, sistema operativo, versión de Python, SHA del código, comando, resultado esperado/obtenido, salida y observaciones.

Plantilla mínima por caso:

| Campo | Valor |
|---|---|
| ID / requisito | AT-… / C-… |
| Estado | PENDIENTE |
| Fecha y entorno | Por registrar |
| SHA evaluado | Por registrar |
| Procedimiento | Comando o pasos exactos |
| Esperado / obtenido | Por registrar |
| Evidencia | Ruta del log, artefacto o URL de CI |
| Observaciones | Incidencias, exclusiones y limitaciones |

## 2. Matriz de aceptación

Todos los casos de esta matriz comienzan en **PENDIENTE** hasta que se adjunte evidencia de ejecución.

| ID | Área / criterio Frente 1 | Procedimiento inicial | Resultado esperado | Tipo |
|---|---|---|---|---|
| AT-ENV-001 | Instalación / C-03, C-05 | Crear venv desde checkout limpio; instalar `pip install -e ".[dev,a2a,api,openai,crypto]"` (confirmar que el extra `crypto` existe antes de usarlo; si no existe, registrar el error y usar extras definidos en `pyproject.toml`) | Instalación reproducible, sin dependencias faltantes; versión y resolución registradas | Manual + CI |
| AT-ENV-002 | Configuración / C-02, C-31 | Revisar `pyproject.toml`, README y variables de entorno; comprobar que no se necesitan secretos para CORE_ONLY | Requisitos documentados; no imprimir ni registrar secretos | Manual |
| AT-CORE-001 | Arranque / C-04, C-26 | Ejecutar `python scripts/clean_room_verify.py` en el checkout objetivo | Exit code 0 y línea final `CLEAN-ROOM: PASS`; conservar salida completa | Automatizada |
| AT-CORE-002 | Auditoría de componentes / C-01, C-09 | Ejecutar `python scripts/audit_matrix.py` | Exit code 0 y 40/40 VERIFIED, o registrar diferencias actuales | Automatizada |
| AT-CORE-003 | Integridad de DNA / C-25 | Ejecutar `python scripts/parity_check.py` | Exit code 0, `is_pure_seed=True`, cero fallos | Automatizada |
| AT-CORE-004 | Contaminación / C-18, C-21 | Ejecutar `python scripts/contamination_scan.py` | Exit code 0; informe sin hallazgos críticos inesperados | Automatizada |
| AT-API-001 | Salud / C-06 | Crear app con Runtime CORE_ONLY y consultar `GET /api/v1/health` con TestClient | HTTP 200 y respuesta JSON válida con estado de salud | Automatizada |
| AT-API-002 | Readiness / C-06 | Consultar `GET /api/v1/readiness` con runtime inicializado | HTTP 200; indicador de readiness coherente con runtime | Automatizada |
| AT-API-003 | Manifest / C-07 | Consultar `GET /api/v1/agent/info` y `GET /api/v1/agent/capabilities` | HTTP exitoso y esquema JSON válido; sin secretos | Automatizada |
| AT-API-004 | Ejecución autorizada / C-08, C-20 | Probar `POST /api/v1/execution` con principal Bearer verificado, regla explícita `execution:run`, grant RBAC y perímetro configurado | Ejecución válida y respuesta según contrato; no asumir que una petición anónima puede ejecutar | Automatizada + integración |
| AT-API-005 | Límites de autorización / C-20, C-21 | Probar endpoint de ejecución sin token, token inválido y principal sin permiso | Rechazo HTTP apropiado; ninguna herramienta se ejecuta | Automatizada |
| AT-API-006 | Trazas / C-20 | Consultar `GET /api/v1/execution/{id}` como propietario y como otro principal | Acceso autorizado para propietario; acceso denegado a no propietario sin `execution:read:any` | Automatizada |
| AT-API-007 | Diagnósticos internos / C-20, C-23 | Consultar `GET /internal/diagnostics` sin permiso y con `diagnostics:read` | Denegación sin permiso; respuesta válida con permiso; sin secretos en salida | Automatizada |
| AT-AGENT-001 | Flujo CORE_ONLY / C-07, C-10 | Ejecutar la prueba golden de flujo y la comprobación clean-room | `Agent.run("hello")` produce AgentResponse válida sin credenciales externas | Automatizada |
| AT-AGENT-002 | Flujos multi-paso / C-10 | Ejecutar `python -m pytest tests/golden/ -q` | Todos los golden flows pasan; registrar cantidad real | Automatizada |
| AT-AGENT-003 | Recuperación / C-11, C-25 | Ejecutar pruebas de resiliencia y recovery existentes | Errores transitorios se recuperan según política; errores de seguridad no se reintentan automáticamente | Automatizada |
| AT-TOOL-001 | Herramientas y permisos / C-09, C-22 | Ejecutar pruebas de tools, firewall, policy y autonomy | Sin regla explícita, la acción externa se deniega; kill switch bloquea acciones | Automatizada |
| AT-SEC-001 | Escaneo de bypasses / C-18, C-20 | Ejecutar las dos pruebas `TestNoSecurityBypass` de `tests/security/test_security_closure.py` | Cero patrones prohibidos; ejecución sin depender de `grep` del sistema | Automatizada; repetir en Windows |
| AT-SEC-002 | Ollama fail-closed / C-11, C-19 | Ejecutar `python -m pytest tests/unit/test_providers.py::test_ollama_provider_raises_if_not_configured -q` con `OLLAMA_BASE_URL` ausente | Se lanza NotImplementedError; no se usa URL localhost implícita | Automatizada; repetir en Windows |
| AT-HITL-001 | SQLite approvals / C-12, C-27 | Ejecutar `python -m pytest tests/security/test_hitl_sqlite_store.py -q` | Todas las pruebas pasan y el entorno temporal se puede limpiar | Automatizada; repetir en Windows |
| AT-HITL-002 | Ledger y crash recovery / C-09, C-11, C-12 | Ejecutar `python -m pytest tests/security/test_hitl_runtime_dispatch_ledger.py -q` | Un efecto externo ambiguo no se vuelve a despachar tras fallo o reinicio | Automatizada; repetir en Windows |
| AT-REG-001 | Suite de regresión / C-25, C-28 | Ejecutar `python -m pytest tests/ -q --ignore=tests/llm_real` | Exit code 0; registrar número real de aprobadas, fallidas, omitidas y warnings | Automatizada |
| AT-REG-002 | Pruebas LLM reales / C-28, C-35 | Evaluar `tests/llm_real/` solo si z-ai CLI y credenciales autorizadas están disponibles | Ejecutadas y documentadas o BLOQUEADAS con causa concreta; nunca contarlas como PASS si se omiten | Condicional |
| AT-WIN-001 | Compatibilidad Windows / C-24 | Repetir AT-SEC-001, AT-SEC-002, AT-HITL-001 y AT-HITL-002 en Windows | Todas pasan en Windows; guardar versión de Windows/Python y salida completa | Manual/CI Windows |
| AT-UI-001 | Frontend real / C-13, C-17 | Localizar el código y comando reales del dashboard; iniciar la interfaz en un entorno de prueba | Interfaz visual real carga y no se confunde con Swagger en `/docs` | Manual; fuente del dashboard aún por localizar |
| AT-UI-002 | Integración UI/API / C-14, C-15 | Desde la interfaz, realizar acción principal y observar la petición al backend | La petición llega al endpoint esperado y la UI representa la respuesta correcta | Manual/integración |
| AT-UI-003 | Errores visibles / C-16, C-23 | Provocar error controlado de API/dependencia desde la interfaz | Mensaje comprensible, sin stack trace sensible ni credenciales | Manual |
| AT-OPS-001 | Reinicio / C-26, C-27 | Arrancar, comprobar health, detener con el procedimiento documentado y arrancar de nuevo | Cierre limpio, sin proceso huérfano ni archivos temporales bloqueados | Manual |
| AT-DOC-001 | Documentación / C-29 a C-32 | Seguir las instrucciones desde un checkout limpio y revisar variables/comandos | Una persona independiente puede instalar, configurar, arrancar, comprobar, diagnosticar y detener | Manual |
| AT-REPORT-001 | Evidencia y versión / C-33 a C-35 | Adjuntar logs, SHA, entorno, omisiones, exclusiones y defectos abiertos | Informe reproducible, con limitaciones explícitas y sin presentar pendientes como PASS | Manual |

## 3. Comandos base

Ejecutar desde la raíz del repositorio, con el entorno virtual activado:

```bash
python -m pytest tests/ -q --ignore=tests/llm_real
python scripts/clean_room_verify.py
python scripts/audit_matrix.py
python scripts/parity_check.py
python scripts/contamination_scan.py
```

En Windows PowerShell, activar el entorno con:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest tests/security/test_security_closure.py -q
python -m pytest tests/unit/test_providers.py::test_ollama_provider_raises_if_not_configured -q
python -m pytest tests/security/test_hitl_sqlite_store.py -q
python -m pytest tests/security/test_hitl_runtime_dispatch_ledger.py -q
```

El comando de instalación debe contrastarse con los extras realmente declarados en `pyproject.toml`: en la versión inspeccionada se ven `openai`, `anthropic`, `groq`, `vector`, `api`, `a2a`, `otel`, `dev` y `all`; no se observa un extra `crypto`. No añadir un extra inexistente al procedimiento definitivo.

## 4. Cobertura existente y brechas conocidas

- Ya hay suites para unit, golden flows, integration, contracts, security, A2A, parity y contamination. La primera acción es reutilizarlas y recoger sus salidas, no duplicarlas.
- `docs/CERTIFICATION.md` identifica la fuente de evidencia de CI previa como commit `979d55d`/merge `4c83885`, no como el commit del PR #50. Por tanto, las cifras de ese documento no certifican automáticamente el nuevo commit.
- El PR #50 reporta 652 pruebas y gates verdes en Linux, pero este documento no afirma que se hayan vuelto a ejecutar.
- El dashboard se describe como externo/no rastreado en el repositorio. AT-UI-001 a AT-UI-003 permanecen bloqueados hasta identificar su fuente y entorno real.
- La suite `tests/llm_real/` está excluida de la suite base y necesita dependencias externas; su estado debe registrarse por separado.
- La aceptación en Windows requiere un runner Windows o ejecución documentada en un equipo Windows. Un resultado Linux no puede sustituirla.
- La instalación limpia debe ejecutarse en un checkout nuevo y venv nuevo; el script clean-room por sí solo no prueba instalación desde cero.

## 5. Criterio de salida del Frente 2

El Frente 2 se considera preparado cuando cada requisito aplicable de C-01 a C-35 está vinculado a uno o más casos, con procedimiento y resultado esperado objetivos. El proyecto no se declara cerrado por completar esta matriz: el cierre requiere ejecutar los casos, conservar evidencia y resolver los fallos críticos.

**Estado actual del Frente 2: batería inicial definida; ejecución y evidencias pendientes.**
