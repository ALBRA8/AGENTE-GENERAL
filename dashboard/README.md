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

## Seguridad

- La API key se envía al backend (`PUT /api/v1/provider/api-key`) donde se
  cifra con Fernet usando `SETTINGS_ENCRYPTION_KEY`.
- La API key NUNCA se almacena en `localStorage`, `sessionStorage`, ni en
  código JavaScript.
- La API key NUNCA se devuelve en un endpoint de lectura — solo
  `api_key_set: bool`.
- Si el usuario guarda sin cambiar la key, se conserva la credencial anterior.
