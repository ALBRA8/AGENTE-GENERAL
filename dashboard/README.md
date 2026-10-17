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

## Stack

- Next.js 16 + TypeScript + CSS variables (sin Tailwind ni shadcn)
- Sin dependencias pesadas

## Ejecutar

```bash
cd dashboard/
npm install
npm run dev   # http://localhost:3000
```

## Variables de entorno

| Variable | Descripción | Default |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | URL del backend ALBRA SEED | `https://agente-general.onrender.com` |
| `DASHBOARD_ACCESS_USER` | Usuario para proteger dashboard y endpoints en producción | Obligatorio en producción |
| `DASHBOARD_ACCESS_PASSWORD` | Contraseña de acceso al dashboard (larga y única) | Obligatorio en producción |
| `INTERNAL_API_SECRET` | Secreto interno compartido con el daemon WhatsApp (mínimo 32 caracteres) | Obligatorio para habilitar el proxy WhatsApp |
| `WHATSAPP_DAEMON_URL` | URL privada del daemon Baileys | `http://localhost:3002` |
| `NVIDIA_API_KEY` | Clave del proveedor, solo del lado servidor | Sin default |

## Seguridad

- La API key se envía al backend (`PUT /api/v1/provider/api-key`) donde se
  cifra con Fernet usando `SETTINGS_ENCRYPTION_KEY`.
- La API key NUNCA se almacena en `localStorage`, `sessionStorage`, ni en
  código JavaScript.
- La API key NUNCA se devuelve en un endpoint de lectura — solo
  `api_key_set: bool`.
- Si el usuario guarda sin cambiar la key, se conserva la credencial anterior.


### Despliegue seguro

- En producción, define `DASHBOARD_ACCESS_USER` y `DASHBOARD_ACCESS_PASSWORD`.
  El proxy de Next.js protege la interfaz y las rutas API con HTTP Basic Auth;
  si faltan estas variables, la aplicación responde `503` en vez de quedar abierta.
- Publica el dashboard únicamente detrás de HTTPS. No envíes credenciales Basic
  Auth por HTTP.
- Define `INTERNAL_API_SECRET` con un valor aleatorio de al menos 32 caracteres
  y exactamente el mismo secreto en el proceso del daemon. No uses valores por
  defecto.
- Mantén el daemon en una red privada; no expongas directamente el puerto 3002.
- El daemon QR/Baileys es una integración no oficial de WhatsApp y requiere
  pruebas de reconexión, duplicados y estabilidad en staging antes de producción.
