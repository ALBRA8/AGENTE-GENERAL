# Fase 3 — Base de despliegue local

**Estado:** implementación inicial; pendiente de ejecución en una máquina objetivo.  
**Rama:** `phase-3-local-deployment-foundation`  
**Objetivo:** preparar una instalación local reproducible del agente sin afirmar que ya existe un despliegue operativo.

## Alcance que podemos adelantar sin el PC

1. Definir requisitos y pasos de instalación local.
2. Incorporar una comprobación previa portable que detecte Python/dependencias faltantes sin mostrar secretos.
3. Separar modo básico `CORE_ONLY` (sin credenciales externas) de modo API/dashboard (dependencias y configuración adicionales).
4. Preparar el protocolo de aceptación que se ejecutará en el PC: arranque, health/readiness, cierre limpio, seguridad de configuración y pruebas de interfaz/API.
5. Registrar los bloqueos de entorno como pendientes, no como PASS.

## Fuera de alcance por ahora

- No instalar ni ejecutar software en el PC del usuario.
- No configurar credenciales reales ni crear secretos en el repositorio.
- No publicar el servicio en Internet ni abrir puertos de red.
- No declarar validado el dashboard, un proveedor externo o el despliegue real sin evidencia de la máquina objetivo.

## Requisitos iniciales

- Windows 10/11 o un sistema compatible; el primer recorrido previsto es Windows PowerShell.
- Python 3.10, 3.11 o 3.12.
- Checkout limpio del repositorio y entorno virtual dedicado.
- Para el servicio HTTP: extras `api` y `a2a`; para ajustes NVIDIA/dashboard, verificar además las instrucciones de `docs/WEB_DASHBOARD_SECURITY.md` y los requisitos de la carpeta `dashboard/`.
- Las credenciales se configuran localmente mediante el gestor de secretos o variables de entorno; nunca se pegan en issues, chat ni archivos versionados.

## Preparación local propuesta

Desde PowerShell, en el directorio del repositorio:

```powershell
py -3.12 --version
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,a2a,api]"
python scripts/local_deployment_preflight.py
```

Si PowerShell bloquea la activación del entorno, no cambies políticas globales del equipo: usa `.venv\Scripts\python.exe` explícitamente para los comandos.

## Criterios de aceptación que faltan en el PC

- [ ] El preflight termina sin bloqueos y su salida queda registrada sin secretos.
- [ ] El comando real de arranque se documenta y funciona desde un entorno virtual limpio.
- [ ] `GET /api/v1/health` devuelve una respuesta saludable y `GET /api/v1/readiness` es coherente.
- [ ] La aplicación se detiene y reinicia sin procesos huérfanos.
- [ ] Los endpoints protegidos rechazan solicitudes anónimas/credenciales inválidas.
- [ ] Si se activa el dashboard, la interfaz funciona contra el backend esperado; no hay credenciales en almacenamiento del navegador ni en assets públicos.
- [ ] Se registra sistema operativo, versión de Python, SHA desplegado, comandos, resultados y errores saneados.

## Evidencia de CI existente

La integración de dashboard de la Fase 2 tuvo una ejecución CI verde en [run #38837703661](https://github.com/ALBRA8/AGENTE-GENERAL/actions/runs/38837703661), SHA `5586a6747d939a91d529bbd23fffc2e7219375b5`. Esto demuestra los jobs ejecutados en CI, no que este procedimiento ya haya sido probado en un PC ni que exista un despliegue de producción.

## Próximo paso

Revisar este cambio y el preflight en CI. Después, cuando haya acceso al PC, ejecutar el procedimiento, capturar evidencia saneada y completar los pasos de aceptación. La Fase 2 y la Fase 3 deben seguir como PRs separadas hasta aprobar su alcance.
