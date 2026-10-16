# AGENTE GENERAL — Dashboard

Dashboard minimalista para configurar el proveedor de IA (Mock o NVIDIA NIM)
del AGENTE GENERAL.

## Características

- Configuración del proveedor: Mock o NVIDIA NIM (OpenAI-compatible)
- Campos: base_url, model, api_key, temperature, top_p, max_tokens, timeout, stream
- Modo claro/oscuro con toggle (respeta el tema del sistema)
- Botón "Probar conexión" — hace una petición mínima al proveedor
- Botón "Restaurar defaults" — vuelve a Mock
- Sin `devIndicators` (sin la "N" de Next.js)
- API key NUNCA se almacena en el frontend — se envía al backend que la cifra

## Ejecutar el dashboard

```bash
cd dashboard/
npm install
npm run dev   # http://localhost:3000
```

## Conectar con la API local

Inicia primero el backend desde la raíz del repositorio en una terminal PowerShell. El backend debe escuchar solo en loopback:

```powershell
$env:ALBRA_CORS_ORIGINS = "http://localhost:3000"
$env:ALBRA_PROVIDER_SETTINGS_TOKEN = "<token aleatorio de al menos 32 caracteres>"
# Opcional para guardar una API key de proveedor: configura SETTINGS_ENCRYPTION_KEY
.\scripts\start_local_api.ps1
```

Genera el token en tu máquina con `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Configúralo solo en el entorno del backend; no lo agregues a `NEXT_PUBLIC_*`, al repositorio ni a un archivo versionado. Si usas el flujo de instalación, ejecuta antes `scripts/setup_local_windows.ps1`.

En otra terminal PowerShell, configura el origen local antes de arrancar Next.js:

```powershell
cd dashboard
$env:NEXT_PUBLIC_API_BASE = "http://127.0.0.1:8000"
npm install
npm run dev
```

Abre `http://localhost:3000` e introduce el token en el campo de acceso del administrador. El token se conserva solo en memoria de la página; después de recargar tendrás que ingresarlo de nuevo. No uses credenciales reales hasta validar el entorno local. Para cualquier host que no sea loopback, exige HTTPS, CORS con origen exacto y controles de acceso revisados.

Prueba primero `http://127.0.0.1:8000/api/v1/health` y `http://127.0.0.1:8000/api/v1/readiness`. Las rutas de configuración `/api/v1/provider/*` deben rechazar solicitudes sin token. Si recibes HTTP 503, revisa que `ALBRA_PROVIDER_SETTINGS_TOKEN` esté configurado en el proceso backend y tenga al menos 32 caracteres; HTTP 401 indica token ausente o incorrecto. CORS debe permitir exactamente el origen que usa el navegador (por ejemplo, `http://localhost:3000`).

## Variables de entorno

| Variable | Dónde | Descripción | Default |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE` | Dashboard | URL base del backend | `https://agente-general.onrender.com` |
| `ALBRA_CORS_ORIGINS` | Backend | Orígenes exactos autorizados, separados por coma | CORS desactivado |
| `ALBRA_PROVIDER_SETTINGS_TOKEN` | Backend | Token bearer de administración (mínimo 32 caracteres) | Sin configurar; rutas cerradas |
| `SETTINGS_ENCRYPTION_KEY` | Backend | Clave Fernet para cifrar la API key guardada | Necesaria para guardar secretos |

## Seguridad

- La API key se envía al backend (`PUT /api/v1/provider/api-key`) donde se cifra con Fernet usando `SETTINGS_ENCRYPTION_KEY`.
- La API key NUNCA se almacena en `localStorage`, `sessionStorage`, ni en código JavaScript persistente.
- La API key NUNCA se devuelve en un endpoint de lectura — solo `api_key_set: bool`.
- El token de administración del dashboard se mantiene en estado de componente y no debe colocarse en variables `NEXT_PUBLIC_*`.
- Si el usuario guarda sin cambiar la key, se conserva la credencial anterior.
- CORS no reemplaza autenticación. No expongas el servidor de desarrollo a la red ni uses HTTP para hosts remotos.
