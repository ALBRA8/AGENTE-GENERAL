# AGENTE GENERAL — Dashboard

Panel minimalista para AGENTE GENERAL. Permite configurar proveedores
LLM (OpenAI-compatible: OpenAI, Anthropic, Groq, Ollama, Z.AI, NVIDIA,
Together, etc.) y probarlos en un chat simple.

## Características

- **3 vistas:** Resumen, Chat, Configuración
- **Modo claro/oscuro** con toggle en el header (respeta el tema del sistema)
- **Sin la "N" de Next.js** — `devIndicators: false` en `next.config.ts`
- **Configuración de proveedores** con plantillas predefinidas que
  coinciden con los defaults del backend (`src/albra_seed/providers/*.py`)
- **Chat directo** al endpoint configurado (sin pasar por el backend)
- **API keys en `localStorage`** — nunca se envían a un servidor intermedio
- **Diseño responsive** — funciona en escritorio y móvil

## Stack

- Next.js 16 (App Router)
- TypeScript 5
- Tailwind CSS (modo `class` para dark mode)
- `next-themes` para el toggle de tema
- Sin shadcn/ui ni dependencias pesadas — solo React + CSS variables

## Ejecutar localmente

```bash
cd dashboard/
npm install      # o: bun install / yarn install / pnpm install
npm run dev      # abre http://localhost:3000
```

## Variables de entorno

| Variable | Descripción | Default |
|---|---|---|
| `NEXT_PUBLIC_BACKEND_URL` | URL del backend ALBRA SEED para la vista Resumen | `http://localhost:8000` |

**No se almacenan API keys en variables de entorno.** Se configuran via
la UI y se guardan en `localStorage` del navegador.

## Configurar un proveedor (ejemplo: NVIDIA GLM-5.3)

1. Ve a **Configuración** → **+ Nuevo proveedor**
2. Selecciona la plantilla **"Personalizado (OpenAI-compatible)"**
3. Completa:
   - **URL base:** `https://integrate.api.nvidia.com/v1`
   - **API key:** `nvapi-...` (tu clave de NVIDIA)
   - **Modelo:** `z-ai/glm-5.3`
4. Guarda y haz clic en **Activar**
5. Ve a **Chat** y prueba

Esto es equivalente al código Python:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key="nvapi-..."
)

completion = client.chat.completions.create(
    model="z-ai/glm-5.3",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
    ],
    temperature=0.5,
    top_p=1,
    max_tokens=1024,
    stream=False,
)
```

## Plantillas predefinidas

Las plantillas coinciden con los defaults del backend:

| Plantilla | URL base | Modelo default |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Anthropic | `https://api.anthropic.com` | `claude-3-5-sonnet-latest` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| Ollama (local) | `http://localhost:11434/v1` | `llama3.1` |
| Z.AI (GLM) | `https://api.z.ai/api/paas/v4` | `glm-4.6` |
| Personalizado | (vacío) | (vacío) |

## Seguridad

- **API keys en `localStorage`** — nunca en código, ni en logs, ni en
  variables de entorno del frontend.
- **Llamadas directas** del navegador al endpoint del proveedor — no
  pasan por ningún servidor intermedio.
- **Sin telemetría** — no se envían datos a terceros.
- **Sin autenticación** en el dashboard — el backend la gestiona
  separadamente cuando está integrado.

## Estructura

```
dashboard/
├── src/
│   ├── app/
│   │   ├── layout.tsx       # ThemeProvider + metadata
│   │   ├── page.tsx         # Resumen
│   │   ├── config/page.tsx  # Configuración de proveedores
│   │   ├── chat/page.tsx    # Chat simple
│   │   └── globals.css      # CSS variables (claro/oscuro)
│   ├── components/
│   │   ├── header.tsx       # Navegación + toggle de tema
│   │   ├── theme-provider.tsx
│   │   └── theme-toggle.tsx
│   └── lib/
│       └── providers.ts     # Storage + tipos + presets
├── next.config.ts           # devIndicators: false (sin la "N")
├── tailwind.config.js       # darkMode: "class"
├── package.json
└── tsconfig.json
```
