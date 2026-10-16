#!/usr/bin/env bash
# start-wa-daemon.sh — Arranca el daemon WhatsApp (Baileys) para el dashboard.
#
# Garantiza que .env.local existe con INTERNAL_API_SECRET >= 32 chars,
# luego lanza src/server/whatsapp-daemon.js en background (nohup) y
# deja logs en logs/wa-daemon.log.
#
# Uso:
#   ./scripts/start-wa-daemon.sh           # arranca en background
#   ./scripts/start-wa-daemon.sh --foreground  # arranca en primer plano (debug)
#   ./scripts/start-wa-daemon.sh --status   # comprueba si está corriendo
#   ./scripts/start-wa-daemon.sh --stop     # detiene el daemon
#
# Requisitos:
#   - Node.js >= 18 y dependencias instaladas (npm ci)
#   - .env.local puede contener NVIDIA_API_KEY si se quiere auto-respuesta con IA

set -euo pipefail

DASH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DASH_DIR"

ENV_FILE="$DASH_DIR/.env.local"
LOG_DIR="$DASH_DIR/logs"
LOG_FILE="$LOG_DIR/wa-daemon.log"
PID_FILE="$DASH_DIR/.wa-daemon.pid"
PORT="${WHATSAPP_DAEMON_PORT:-3002}"

# Read only known KEY=value entries; never execute .env.local as shell code.
load_env_safely() {
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    case "$key" in
      INTERNAL_API_SECRET|WHATSAPP_DAEMON_PORT|WHATSAPP_DAEMON_HOST|WHATSAPP_DAEMON_URL|NVIDIA_API_KEY|NVIDIA_ASR_ENDPOINT|NVIDIA_ASR_POLL_SECONDS|NVIDIA_ASR_TIMEOUT_MS|WHATSAPP_ASR_ZAI_FALLBACK|MAX_MEDIA_BYTES|MAX_CONCURRENT_MEDIA|MAX_MEDIA_QUEUE|MAX_RECONNECT_ATTEMPTS)
        export "$key=$value"
        ;;
    esac
  done < "$ENV_FILE"
}

mkdir -p "$LOG_DIR"

# ----- helpers -----
gen_secret() {
  # 32 bytes hex = 64 chars, cumple el mínimo de 32 chars del daemon
  openssl rand -hex 32
}

ensure_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "[start-wa-daemon] Creando $ENV_FILE con INTERNAL_API_SECRET generado"
    cat > "$ENV_FILE" <<EOF
# LOCAL — NO COMMITAR (.gitignore)
# Generado por scripts/start-wa-daemon.sh

INTERNAL_API_SECRET=$(gen_secret)
WHATSAPP_DAEMON_URL=http://localhost:${PORT}
MAX_MEDIA_BYTES=8388608
MAX_CONCURRENT_MEDIA=2
MAX_MEDIA_QUEUE=20
MAX_RECONNECT_ATTEMPTS=10
EOF
    echo "[start-wa-daemon] .env.local escrito. Edítalo para agregar NVIDIA_API_KEY si necesitas auto-respuesta con IA."
  fi

  # Validar que INTERNAL_API_SECRET está y mide >= 32 chars
  if ! grep -qE '^INTERNAL_API_SECRET=.{32,}$' "$ENV_FILE"; then
    echo "[start-wa-daemon] ERROR: INTERNAL_API_SECRET no configurado o demasiado corto en $ENV_FILE" >&2
    exit 1
  fi
}

is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

probe_health() {
  # /status requiere Authorization: Bearer $INTERNAL_API_SECRET
  local secret
  secret="$(grep -E '^INTERNAL_API_SECRET=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
  curl -fsS -m 2 -H "Authorization: Bearer ${secret}" \
    "http://localhost:${PORT}/status" >/dev/null 2>&1
}

cmd_start() {
  if is_running && probe_health; then
    echo "[start-wa-daemon] El daemon ya está corriendo (pid=$(cat "$PID_FILE"))"
    return 0
  fi

  ensure_env

  echo "[start-wa-daemon] Arrancando daemon en puerto $PORT ..."
  echo "[start-wa-daemon] Log: $LOG_FILE"

  # Cargar únicamente variables permitidas; no ejecutar contenido del archivo
  load_env_safely
  PORT="${WHATSAPP_DAEMON_PORT:-3002}"

  nohup node "$DASH_DIR/src/server/whatsapp-daemon.js" > "$LOG_FILE" 2>&1 &
  local pid=$!
  echo "$pid" > "$PID_FILE"

  # Esperar hasta 8s a que escuche en /status
  local i
  local secret_for_probe
  secret_for_probe="$(grep -E '^INTERNAL_API_SECRET=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
  for i in 1 2 3 4 5 6 7 8; do
    if probe_health; then
      echo "[start-wa-daemon] OK — daemon corriendo (pid=$pid, port=$PORT)"
      echo "[start-wa-daemon] Estado: $(curl -fsS -m 2 -H "Authorization: Bearer ${secret_for_probe}" "http://localhost:${PORT}/status" || echo '<no-response>')"
      return 0
    fi
    sleep 1
  done

  echo "[start-wa-daemon] ERROR: el daemon no respondió /status en 8s" >&2
  echo "[start-wa-daemon] Últimas 20 líneas del log:" >&2
  tail -n 20 "$LOG_FILE" >&2 || true
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  exit 1
}

cmd_foreground() {
  ensure_env
  load_env_safely
  PORT="${WHATSAPP_DAEMON_PORT:-3002}"
  exec node "$DASH_DIR/src/server/whatsapp-daemon.js"
}

cmd_stop() {
  if ! is_running; then
    echo "[start-wa-daemon] No está corriendo"
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  echo "[start-wa-daemon] Enviando SIGTERM a pid=$pid"
  kill -TERM "$pid" 2>/dev/null || true
  local i
  for i in 1 2 3 4 5; do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "[start-wa-daemon] SIGKILL"
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  echo "[start-wa-daemon] Detenido"
}

cmd_status() {
  if is_running && probe_health; then
    local secret
    secret="$(grep -E '^INTERNAL_API_SECRET=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
    echo "RUNNING pid=$(cat "$PID_FILE") port=$PORT"
    curl -fsS -H "Authorization: Bearer ${secret}" \
      "http://localhost:${PORT}/status" || true
    echo
  else
    echo "STOPPED"
    exit 1
  fi
}

case "${1:-start}" in
  start)       cmd_start ;;
  --foreground) cmd_foreground ;;
  --status)    cmd_status ;;
  --stop)      cmd_stop ;;
  -h|--help)
    sed -n '1,20p' "${BASH_SOURCE[0]}"
    ;;
  *)
    echo "Uso: $0 {start|--foreground|--status|--stop}" >&2
    exit 2
    ;;
esac
