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

## Daemon WhatsApp (Baileys)

El dashboard no se conecta directamente a WhatsApp: lo hace a través de un
daemon Node.js independiente (Baileys) que escucha en `http://localhost:3002`.
Si la pestaña WhatsApp del dashboard muestra **"Daemon no disponible"** o
**"No se pudo conectar. ¿Está el daemon corriendo?"**, es porque el proceso
del daemon no está activo — no es un bug de código.

### Arranque rápido

```bash
# Desde dashboard/ — crea .env.local si falta, arranca el daemon en background
npm run wa-daemon:start

# Verificar que está corriendo (responde /status con auth)
npm run wa-daemon:status

# Detener
npm run wa-daemon:stop

# Primer plano (debug, muestra todos los logs en consola)
bash scripts/start-wa-daemon.sh --foreground
```

El script `scripts/start-wa-daemon.sh` se encarga de:

1. Generar `.env.local` con `INTERNAL_API_SECRET` aleatorio (64 hex chars)
   si no existe. **El archivo está en `.gitignore` — nunca se commitea.**
2. Validar que el secreto tenga al menos 32 caracteres.
3. Lanzar `node src/server/whatsapp-daemon.js` con `nohup`, PID en
   `.wa-daemon.pid`, logs en `logs/wa-daemon.log`.
4. Esperar hasta 8 s a que `/status` responda 200 con auth Bearer.
5. Si hay sesión previa en `/tmp/wa-session`, auto-conecta sin necesidad de QR.

### Variables que lee el daemon

| Variable | Descripción | Default |
|---|---|---|
| `INTERNAL_API_SECRET` | Token Bearer compartido con el dashboard (≥ 32 chars) | Obligatorio |
| `WHATSAPP_DAEMON_PORT` | Puerto HTTP del daemon | `3002` |
| `WHATSAPP_DAEMON_HOST` | Interfaz de escucha del daemon; mantener en loopback salvo red privada controlada | `127.0.0.1` |
| `MAX_MEDIA_BYTES` | Límite de tamaño por media descargada | `8388608` (8 MB) |
| `MAX_CONCURRENT_MEDIA` | Procesos de media simultáneos | `2` |
| `MAX_MEDIA_QUEUE` | Mensajes encolados esperando slot | `20` |
| `MAX_RECONNECT_ATTEMPTS` | Reintentos antes de `max_reconnects` | `10` |
| `NVIDIA_API_KEY` | Clave de NVIDIA para IA y transcripción | Sin default |
| `NVIDIA_ASR_ENDPOINT` | URL HTTPS explícita del endpoint ASR de NVIDIA validado para este entorno | Obligatorio para transcribir audio |
| `NVIDIA_ASR_POLL_SECONDS` | Ventana de polling de NVCF en segundos (1–300) | `120` |
| `NVIDIA_ASR_TIMEOUT_MS` | Timeout total de la petición ASR en milisegundos (1.000–300.000) | `135000` |

WhatsApp audio transcription also has an automatic Z.ai SDK fallback when the NVIDIA ASR request fails. The daemon sends the same bounded audio buffer as base64 to `z-ai-web-dev-sdk`; the existing chat model remains unchanged. Set `WHATSAPP_ASR_ZAI_FALLBACK=false` to disable the fallback (enabled by default). Ensure the runtime has the package installed and the Z.ai SDK environment configured.

### Notas de voz / transcripción

Para transcribir audios, el daemon necesita `NVIDIA_API_KEY` y `NVIDIA_ASR_ENDPOINT`. No hay un endpoint predeterminado porque el deployment NVCF probado anteriormente devolvió errores de proveedor; configura aquí únicamente una URL HTTPS que hayas verificado con una prueba real de audio:

```dotenv
NVIDIA_API_KEY=tu_clave
# Opcional: solo si necesitas sobrescribir el endpoint predeterminado
# NVIDIA_ASR_ENDPOINT=https://tu-endpoint-real/v1/audio/transcriptions
# Opcionales: ventana de polling NVCF y timeout total
# NVIDIA_ASR_POLL_SECONDS=120
# NVIDIA_ASR_TIMEOUT_MS=135000
```

El endpoint ASR es independiente de `https://integrate.api.nvidia.com/v1/chat/completions`; no uses el endpoint de chat para notas de voz. El daemon envía multipart con `language=es-US`, `response_format=json` y el audio OGG. Si el endpoint devuelve 5xx/504, cambiar el timeout del cliente no repara un worker NVCF atascado: valida/recrea el deployment o configura un endpoint ASR operativo antes de producción. No compartas la clave en el chat ni la pegues en logs. Si la clave que compartiste previamente era completa y real, revócala y genera otra en NVIDIA antes de probar.

Después de configurar esas variables, reinicia **solo el daemon en el entorno de prueba** para que las lea. Envía una nota de voz corta en español desde un chat individual y revisa `dashboard/logs/wa-daemon.log`. El daemon registra códigos de fallo (por ejemplo, `ASR_NOT_CONFIGURED`, `ASR_HTTP_ERROR` o `ASR_EMPTY_TRANSCRIPT`), no la clave ni el contenido transcrito. Si falla, comparte únicamente el código y el estado HTTP, nunca secretos.

Las pruebas automáticas simulan las respuestas del proveedor; no validan la URL real ni sustituyen una nota de voz real.

### Diagnóstico

```bash
# Daemon no responde
ss -tlnp | grep 3002          # debería ver un listener
curl -fsS -H "Authorization: Bearer $INTERNAL_API_SECRET" \
  http://localhost:3002/status

# Logs del daemon
tail -f dashboard/logs/wa-daemon.log

# Sesión Baileys corrupta — borrar y re-escanear QR
rm -rf /tmp/wa-session
npm run wa-daemon:stop && npm run wa-daemon:start
```

### Reiniciar el dashboard Next.js

Después de crear o modificar `.env.local`, **reinicia el proceso de Next.js
dev** para que relea las variables (Next.js cachea `process.env` al arrancar):

```bash
# Desde dashboard/, en tu terminal (no en background)
npm run dev
```

Si el dashboard sigue devolviendo 503 con `daemon_offline` tras reiniciar,
verifica que `INTERNAL_API_SECRET` en el dashboard coincida con el secreto
que el daemon cargó desde el mismo `.env.local`.

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
- El daemon escucha por defecto en `127.0.0.1`; solo cambia `WHATSAPP_DAEMON_HOST` a una interfaz privada si dashboard y daemon necesitan comunicarse entre hosts. No uses `0.0.0.0` en un servidor accesible desde Internet.
- El script de arranque lee variables permitidas de `.env.local` sin ejecutarlo como código shell.
- El endpoint interno `/send` solo acepta destinatarios individuales con número telefónico y limita los mensajes a 4.000 caracteres.
- Mantén el daemon en una red privada; no expongas directamente el puerto 3002.
- El daemon QR/Baileys es una integración no oficial de WhatsApp y requiere
  pruebas de reconexión, duplicados y estabilidad en staging antes de producción.

## Configuración del proveedor de IA

La pantalla de configuración de proveedores está disponible en `/providers`. Usa el token de administración `ALBRA_PROVIDER_SETTINGS_TOKEN` en memoria de la página y guarda las claves de proveedor cifradas en el backend; no publiques ese token ni la clave de proveedor en variables `NEXT_PUBLIC_*`. Antes de usar credenciales reales, configura `ALBRA_PROVIDER_SETTINGS_TOKEN`, `SETTINGS_ENCRYPTION_KEY` y `ALBRA_CORS_ORIGINS` en el backend, y valida la integración en staging.
