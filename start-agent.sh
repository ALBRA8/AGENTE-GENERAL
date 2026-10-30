#!/usr/bin/env bash
# AGENTE-GENERAL — Doble clic para arrancar todo
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD_DIR="$SCRIPT_DIR/dashboard"
cd "$DASHBOARD_DIR"

echo "═══════════════════════════════════════════════════════════"
echo "       AGENTE-GENERAL — Iniciando..."
echo "═══════════════════════════════════════════════════════════"

# 1. Verificar Node.js
if ! command -v node &>/dev/null; then
  echo "❌ Node.js no instalado. Descarga: https://nodejs.org/"
  read; exit 1
fi
echo "✓ Node.js $(node --version)"

# 2. Instalar deps
if [ ! -d "node_modules" ]; then
  echo "Instalando dependencias..."
  npm install --no-audit --no-fund 2>&1 | tail -3
fi
echo "✓ Dependencias OK"

# 3. Crear .env.local
if [ ! -f ".env.local" ]; then
  SECRET=$(openssl rand -hex 32 2>/dev/null || node -e "console.log(require('crypto').randomBytes(32).toString('hex'))")
  cat > .env.local <<EOF
INTERNAL_API_SECRET=${SECRET}
WHATSAPP_DAEMON_URL=http://localhost:3002
MAX_MEDIA_BYTES=8388608
MAX_CONCURRENT_MEDIA=2
MAX_MEDIA_QUEUE=20
MAX_RECONNECT_ATTEMPTS=10
NVIDIA_API_KEY=pon-aqui-tu-nvidia-api-key
GROQ_API_KEY=pon-aqui-tu-groq-api-key
EOF
  echo "✓ .env.local creado"
else
  echo "✓ .env.local existe"
fi

# 4. Limpiar sesión corrupta
SESSION_DIR="$HOME/.local/share/albra/whatsapp/default"
if [ -f "$SESSION_DIR/creds.json" ]; then
  if ! node -e "JSON.parse(require('fs').readFileSync('$SESSION_DIR/creds.json','utf8'))" 2>/dev/null; then
    rm -rf "$SESSION_DIR"; echo "✓ Sesión corrupta limpiada"
  else
    echo "✓ Sesión existente OK"
  fi
else
  echo "✓ Sin sesión previa"
fi

# 5. Matar procesos anteriores
pkill -f 'whatsapp-daemon.js' 2>/dev/null || true
pkill -f 'next dev' 2>/dev/null || true
sleep 2

# 6. Arrancar daemon
echo "Arrancando daemon WhatsApp..."
if [ -f "scripts/start-wa-daemon.sh" ]; then
  bash scripts/start-wa-daemon.sh start 2>&1 | head -5
else
  node src/server/whatsapp-daemon.js &
fi
sleep 3

# 7. Arrancar dashboard
echo "Arrancando dashboard..."
nohup node_modules/.bin/next dev -p 3000 > /tmp/agente-dashboard.log 2>&1 &
for i in $(seq 1 20); do
  curl -sS -m 1 http://localhost:3000/ >/dev/null 2>&1 && break
  sleep 1
done

# 8. Abrir navegador
URL="http://localhost:3000"
command -v xdg-open &>/dev/null && xdg-open "$URL" 2>/dev/null || true
command -v open &>/dev/null && open "$URL" 2>/dev/null || true

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✅ AGENTE-GENERAL corriendo"
echo "═══════════════════════════════════════════════════════════"
echo "  Dashboard: http://localhost:3000"
echo "  Daemon:    http://localhost:3002/status"
echo ""
echo "  Para conectar WhatsApp:"
echo "  1. Pestaña WhatsApp → Conectar por QR"
echo "  2. Escanea el QR desde tu celular"
echo ""
echo "  Ctrl+C para detener todo"
echo "═══════════════════════════════════════════════════════════"

cleanup() { pkill -f 'whatsapp-daemon.js' 2>/dev/null; pkill -f 'next dev' 2>/dev/null; exit 0; }
trap cleanup SIGINT SIGTERM
wait
