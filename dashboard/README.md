# AGENTE GENERAL — Dashboard de Supervisión HITL

Dashboard web minimalista para supervisar el estado del agente AGENTE
GENERAL, revisar aprobaciones pendientes (HITL) y consultar ejecuciones.

## Stack

- **Framework:** Next.js 16 (App Router) + TypeScript 5
- **Estilo:** Tailwind CSS 4 + shadcn/ui (componentes New York)
- **Testing:** vitest
- **Package manager:** bun (también compatible con npm/yarn)

## Vistas

1. **Resumen** — estado del agente, aprobaciones pendientes, componentes
   del runtime, último error, última actualización
2. **Aprobaciones** — lista de solicitudes pendientes con detalle, aprobar
   / rechazar con confirmación explícita
3. **Historial** — tabla responsive con filtros, consulta de traza
   individual por execution_id
4. **Sistema** — estado de API, persistencia, backups (solo si hay evidencia)

## Diseño

- Minimalista: fondo neutro, tipografía legible, pocos colores
- Responsive: navegación lateral en escritorio, menú hamburguesa en móvil
- Estados identificados con texto + iconos (no solo colores)
- Sin gráficos decorativos ni animaciones innecesarias
- Badge "DEMO" visible cuando los datos son simulados

## Seguridad

- **Denegar por defecto** cuando no se pueda verificar autorización
- **Sin tokens/secretos en frontend** — cookies de sesión httpOnly,
  Secure, SameSite=Strict gestionadas por el backend
- **Confirmación explícita** antes de aprobar o rechazar
- **Prevención de doble-click** — las aprobaciones en vuelo se trackean
- **Sin reintentos automáticos** de acciones externas inciertas
- **Datos simulados claramente identificados** con badge "DEMO"
- **El backend es la autoridad** — el dashboard no ejecuta nada

## Ejecutar localmente

```bash
cd dashboard/
bun install        # o npm install / yarn install
bun run dev        # abre http://localhost:3000
```

## Scripts

```bash
bun run dev          # servidor de desarrollo (puerto 3000)
bun run lint         # ESLint
bun run test         # vitest (tests unitarios)
bun run build       # build de producción (no usar en dev)
```

## Variables de entorno

| Variable | Descripción | Default |
|---|---|---|
| `NEXT_PUBLIC_ALBRA_DEMO_MODE` | `=1` fuerza modo demo (datos simulados, sin llamadas al backend) | no seteado |

**No se almacenan tokens, contraseñas ni secretos en variables de entorno
del frontend.** La autenticación la gestiona el backend vía cookies httpOnly.

## Integración con el backend

El dashboard usa las siguientes rutas del backend ALBRA SEED:

| Ruta | Método | Estado | Uso |
|---|---|---|---|
| `/api/v1/health` | GET | ✅ Existe | Estado del runtime |
| `/api/v1/readiness` | GET | ✅ Existe | Bool readiness probe |
| `/api/v1/agent/info` | GET | ✅ Existe | Información del agente |
| `/api/v1/execution/{id}` | GET | ✅ Existe | Traza de ejecución |
| `/api/v1/approvals/pending` | GET | ❌ No existe | Listar aprobaciones pendientes |
| `/api/v1/approvals/{id}/decision` | POST | ❌ No existe | Aprobar/rechazar |
| `/api/v1/approvals/{id}/resume` | POST | ❌ No existe | Reanudar tras decisión |

### Integración pendiente

Las rutas de aprobaciones **no existen en el backend**. El componente
`ApprovalEngine` existe en `src/albra_seed/approvals/engine.py` pero no
está conectado a ninguna ruta HTTP. Implementar estas rutas requiere
cambios en el backend que deben pasar por el proceso de revisión del
auditor independiente.

El dashboard muestra "No disponible" con identificación clara de los
endpoints pendientes en la vista de Aprobaciones. Cuando el backend
implemente las rutas, el dashboard las usará automáticamente.

## Pruebas

```bash
bun run test
```

11 tests cubren:

- `fetchHealth` — éxito 200, error de red, error 500
- `fetchPendingApprovals` — éxito 200, 404 (no disponible), error de red
- `postApprovalDecision` — éxito 200, 404, error de red
- `fetchSystemStatus` — agregación correcta, fallo total
- Verifica que `persistence_status` y `last_backup_verified` siempre son
  "unknown" (no se afirma verificado sin evidencia)

## Limitaciones pendientes

1. **Aprobaciones no disponibles** — el backend no expone rutas de
   aprobaciones. El dashboard muestra el estado de integración pendiente
   y no procesa decisiones (ni en modo demo).
2. **Listado de ejecuciones** — el backend no expone un endpoint de
   listado. El historial usa datos de demostración claramente etiquetados.
   La consulta de traza individual por execution_id SÍ funciona contra
   el backend real.
3. **Persistencia y backups** — el backend no expone estado de
   persistencia ni información de backups. El dashboard reporta
   "desconocido" / "no verificado" sin afirmar nada sin evidencia.
4. **Sin despliegue en producción** — el dashboard está en modo
   desarrollo. No se desplegó ni se modificó la configuración de
   seguridad del backend.

## Archivos principales

```
dashboard/
├── src/
│   ├── app/
│   │   ├── layout.tsx              # Metadata + shell
│   │   └── page.tsx                # Página principal
│   ├── components/
│   │   ├── dashboard/
│   │   │   ├── shell.tsx           # Layout responsive
│   │   │   ├── summary-view.tsx    # Vista Resumen
│   │   │   ├── approvals-view.tsx  # Vista Aprobaciones
│   │   │   ├── history-view.tsx    # Vista Historial
│   │   │   └── system-view.tsx     # Vista Sistema
│   │   └── ui/                     # shadcn/ui components
│   └── lib/
│       ├── api.ts                  # Cliente API
│       └── __tests__/
│           └── api.test.ts         # 11 tests
├── package.json
├── tsconfig.json
├── next.config.ts
├── tailwind.config.ts
└── vitest.config.ts
```

## No incluido

- No se modificó el backend ALBRA SEED
- No se añadió autenticación al dashboard (el backend la gestiona)
- No se desplegó en producción
- No se modificó la configuración de seguridad del backend
