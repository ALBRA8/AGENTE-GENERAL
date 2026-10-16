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
6. Proporcionar un entrypoint ASGI para la API y scripts PowerShell de instalación y arranque local.

## Fuera de alcance por ahora

- No instalar ni ejecutar software en el PC del usuario.
- No configurar credenciales reales ni crear secretos en el repositorio.
- No publicar el servicio en Internet ni abrir puertos de red.
- No declarar validado el dashboard, un proveedor externo o el despliegue real sin evidencia de la máquina objetivo.

## Requisitos iniciales

- Windows 10/11 o un sistema compatible; el primer recorrido previsto es Windows PowerShell.
- Python 3.10 o superior instalado y disponible mediante el launcher `py`.
- Checkout limpio del repositorio y entorno virtual dedicado.
- Para el servicio HTTP: extras `api` y `a2a`; para ajustes NVIDIA/dashboard, verificar además las instrucciones de `docs/WEB_DASHBOARD_SECURITY.md` y los requisitos de la carpeta `dashboard/`.
- Las credenciales se configuran localmente mediante el gestor de secretos o variables de entorno; nunca se pegan en issues, chat ni archivos versionados.

## Preparación y arranque local en Windows

Desde PowerShell, en el directorio del repositorio:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\setup_local_windows.ps1
.\scripts\start_local_api.ps1
```

El cambio de política solo afecta a la sesión actual de PowerShell. Si la política corporativa no permite scripts, sigue la política aprobada por tu organización. El instalador crea `.venv`, instala `.[dev,a2a,api]`, ejecuta el preflight y verifica la versión del CLI. Para omitir dependencias de desarrollo: `.\scripts\setup_local_windows.ps1 -SkipDevDependencies`.

El servidor se enlaza intencionalmente a `127.0.0.1` (no a `0.0.0.0`), para no exponer la API a la red local por defecto. Para cambiar el puerto: `.\scripts\start_local_api.ps1 -Port 8010`.

Con el servidor iniciado, verificar en el navegador:

- `http://127.0.0.1:8000/api/v1/health`
- `http://127.0.0.1:8000/api/v1/readiness`

Como alternativa manual, el entorno virtual puede usarse sin activar el entorno:

```powershell
.\.venv\Scripts\python.exe scripts/local_deployment_preflight.py
.\.venv\Scripts\python.exe -m uvicorn albra_seed.local_api:app --host 127.0.0.1 --port 8000
```

## Criterios de aceptación que faltan en el PC

- [ ] El instalador y el preflight terminan sin bloqueos y la salida queda registrada sin secretos.
- [ ] La API arranca desde un entorno virtual limpio y la aplicación se detiene y reinicia sin procesos huérfanos.
- [ ] `GET /api/v1/health` devuelve una respuesta saludable y `GET /api/v1/readiness` es coherente.
- [ ] Los endpoints protegidos rechazan solicitudes anónimas/credenciales inválidas.
- [ ] Si se activa el dashboard, la interfaz funciona contra el backend esperado; no hay credenciales en almacenamiento del navegador ni en assets públicos.
- [ ] Se registra sistema operativo, versión de Python, SHA desplegado, comandos, resultados y errores saneados.

## Evidencia de CI existente

La integración de dashboard de la Fase 2 tuvo una ejecución CI verde en [run #38837703661](https://github.com/ALBRA8/AGENTE-GENERAL/actions/runs/38837703661), SHA `5586a6747d939a91d529bbd23fffc2e7219375b5`. La base de despliegue de la Fase 3 tuvo CI verde en [run #38839316403](https://github.com/ALBRA8/AGENTE-GENERAL/actions/runs/38839316403), SHA `c52e60815576ed433d50b44069aa0b2aad1e446f`. Estas ejecuciones demuestran los jobs ejecutados en CI, no que el procedimiento ya haya sido probado en el PC ni que exista un despliegue de producción.

## Seguridad y siguiente paso

El preflight no muestra valores de secretos. No se necesita una clave de proveedor para probar el runtime básico. La configuración del panel de proveedores exige los controles descritos en `docs/WEB_DASHBOARD_SECURITY.md` antes de guardar credenciales. No publiques el servicio ni abras puertos de router/firewall como parte de esta prueba local.

La Fase 2 y la Fase 3 deben seguir como PRs separadas hasta aprobar su alcance. La aceptación en máquina real, el acoplamiento dashboard/API y cualquier control de producción siguen pendientes.
