/**
 * WhatsApp Daemon — Baileys + IA auto-reply (text, audio, images)
 *
 * Hardened version with:
 *   - Bounded media download (abort if > MAX_MEDIA_BYTES received)
 *   - Message deduplication (in-memory Set of message IDs)
 *   - Reconnection with exponential backoff + jitter
 *   - Max reconnection attempts (distinct from logged-out)
 *   - Duplicate listener prevention (removeAllListeners before re-registering)
 *   - Concurrency limiter for media processing
 *   - Log sanitization (no phone numbers, no message content, no transcriptions)
 *   - Resource cleanup on shutdown
 *
 * Security:
 *   - Bearer token (INTERNAL_API_SECRET, min 32 chars)
 *   - API keys in .env.local (never logged)
 *   - Session in /tmp/wa-session (ephemeral, mode 0700)
 *   - Temp audio files with mode 0600, always cleaned up
 */

const http = require("http");
const fs = require("fs");
const path = require("path");
const { timingSafeEqual } = require("crypto");

// OpenClaw-adapted utilities (MIT-licensed, attributed in THIRD_PARTY_NOTICES.md)
const {
  resolveSessionDir,
  ensureSessionDir,
  restoreCredsFromBackupIfNeeded,
  resolveReconnectPolicy,
  computeBackoff,
  createWatchdog,
  WHATSAPP_WATCHDOG_TIMEOUT_ERROR,
  acquireConnectionOwnerLock,
  WhatsAppConnectionOwnerBusyError,
  createPersistentDedupStore,
} = require("./openclaw-adapted");

// Load .env.local manually
const envPath = path.join(__dirname, "..", "..", ".env.local");
try {
  const envContent = fs.readFileSync(envPath, "utf-8");
  for (const line of envContent.split("\n")) {
    const match = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (match && !process.env[match[1]]) {
      process.env[match[1]] = match[2].trim();
    }
  }
  console.log("[WA-DAEMON] Loaded .env.local");
} catch {
  console.log("[WA-DAEMON] No .env.local found, using process.env");
}

const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, downloadContentFromMessage } = require("@whiskeysockets/baileys");
const QRCode = require("qrcode");
const P = require("pino");

// === Configuration ===
const PORT = process.env.WHATSAPP_DAEMON_PORT || 3002;
// Keep the privileged daemon private by default. Override only for a deliberate private-network deployment.
const HOST = process.env.WHATSAPP_DAEMON_HOST || "127.0.0.1";
const SECRET = process.env.INTERNAL_API_SECRET;
const MAX_MEDIA_BYTES = Math.max(1, parseInt(process.env.MAX_MEDIA_BYTES || (8 * 1024 * 1024).toString(), 10) || (8 * 1024 * 1024));
const MAX_CONCURRENT_MEDIA = Math.max(1, parseInt(process.env.MAX_CONCURRENT_MEDIA || "2", 10) || 2);
const MAX_MEDIA_QUEUE = Math.max(0, parseInt(process.env.MAX_MEDIA_QUEUE || "20", 10) || 0);

// Reconnect policy — adapted from OpenClaw DEFAULT_RECONNECT_POLICY:
//   initial 2s, max 30s, factor 1.8, jitter 0.25, maxAttempts 12
// Replaces the previous hard-coded MAX_RECONNECT_ATTEMPTS=10 + base 1s + 0-999ms random jitter.
const RECONNECT_POLICY = resolveReconnectPolicy({
  maxAttempts: parseInt(process.env.MAX_RECONNECT_ATTEMPTS || "12", 10),
});

// Persistent session directory — replaces ephemeral /tmp/wa-session.
// /tmp gets wiped on every Render deploy, forcing a QR rescan on every restart.
// The new location is <DATA_DIR>/whatsapp/<accountId>/ which persists across deploys.
const WHATSAPP_ACCOUNT_ID = process.env.WHATSAPP_ACCOUNT_ID || "default";
const SESSION_DIR = resolveSessionDir(WHATSAPP_ACCOUNT_ID);

// Persistent dedup store — replaces the in-memory Map (TTL 60s) which lost
// all entries on daemon restart, causing duplicate replies after a crash.
// The new store uses sha256(remoteJid+msgId), 7-day TTL, 5000 entries (OpenClaw).
let dedupStore = null;
let connectionOwnerLease = null;
let watchdog = null;
let cachedBaileysVersion = null; // Hermes pattern: cache last-good version for fallback
let connectionStartTime = null; // OpenClaw pattern: track connection age for backoff reset

if (!SECRET || SECRET.length < 32) {
  throw new Error("INTERNAL_API_SECRET must be configured with at least 32 characters");
}

// === State ===
let sock = null;
let lastQR = null;
let connectionState = "disconnected";
let connectedPhone = null;
let reconnectAttempts = 0;
let reconnectTimer = null;
let isShuttingDown = false;
let startPromise = null;
let connectionGeneration = 0;

// Dedup is now persistent (see dedupStore above and createPersistentDedupStore)
// The old in-memory Map with 60s TTL was lost on every restart, causing
// duplicate replies after a crash. The new store survives restarts via
// a JSON file in SESSION_DIR/dedup.json (mode 0o600).

// Concurrency limiter for media processing
let activeMediaTasks = 0;
const mediaQueue = [];
const ownedTempFiles = new Set();

const logger = P({ level: "silent" });

// === Logging (sanitized — no phone numbers, no content) ===
function log(msg) {
  console.log(`[WA-DAEMON] ${new Date().toISOString()} ${msg}`);
}

function sanitizeJid(jid) {
  // Redact phone numbers in JIDs: "573163301286@s.whatsapp.net" → "****@s.whatsapp.net"
  if (typeof jid !== "string" || !jid) return "unknown";
  return jid.replace(/^\d+/, "****");
}

// === Identity ===
function getIdentity() {
  try {
    const idFile = path.join(process.cwd(), ".agent-identity.json");
    const data = JSON.parse(fs.readFileSync(idFile, "utf-8"));
    return { name: data.name || "AGENTE GENERAL", role: data.role || "asistente de IA" };
  } catch {
    return { name: "AGENTE GENERAL", role: "asistente de IA" };
  }
}

// === Dedup (persistent, OpenClaw-adapted) ===
// Replaces the old in-memory Map with TTL 60s. The new store:
//   - Survives daemon restarts (JSON file in SESSION_DIR/dedup.json)
//   - Uses sha256(remoteJid+msgId) as key (no cross-chat collisions)
//   - 7-day TTL (vs previous 60s)
//   - 5000 entries cap with LRU prune
//   - Auto-disables on disk failure so message handling never breaks
//
// Initialize eagerly so tests can call isDuplicate() without first
// starting the full WhatsApp connection. The store loads its state
// from disk lazily on first use (see createPersistentDedupStore).
dedupStore = createPersistentDedupStore(SESSION_DIR);

async function isDuplicate(remoteJid, msgId) {
  if (!dedupStore) return false;
  try {
    return await dedupStore.isDuplicate(remoteJid, msgId);
  } catch {
    // Best-effort: if dedup store fails, don't block message processing.
    // The store auto-disables on repeated failures (OpenClaw behavior).
    return false;
  }
}

// === Concurrency limiter ===
async function withMediaSlot(fn) {
  if (isShuttingDown) throw new Error("daemon_shutting_down");

  if (activeMediaTasks >= MAX_CONCURRENT_MEDIA) {
    if (mediaQueue.length >= MAX_MEDIA_QUEUE) {
      throw new Error("media_queue_full");
    }
    await new Promise((resolve, reject) => mediaQueue.push({ resolve, reject }));
    // The released slot is transferred to this waiter; do not increment again.
  } else {
    activeMediaTasks++;
  }

  try {
    return await fn();
  } finally {
    const next = mediaQueue.shift();
    if (next && !isShuttingDown) {
      next.resolve();
    } else {
      activeMediaTasks--;
      if (next) next.reject(new Error("daemon_shutting_down"));
    }
  }
}

// Collect a Baileys media stream while enforcing the byte limit during download.
async function collectMediaStreamBounded(stream, maxBytes = MAX_MEDIA_BYTES) {
  const chunks = [];
  let totalBytes = 0;

  try {
    for await (const chunk of stream) {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      totalBytes += bytes.length;
      if (totalBytes > maxBytes) {
        if (typeof stream.destroy === "function") stream.destroy();
        throw new Error("media_too_large_actual");
      }
      chunks.push(bytes);
    }
  } catch (error) {
    if (typeof stream.destroy === "function" && !stream.destroyed) stream.destroy();
    throw error;
  }

  return Buffer.concat(chunks, totalBytes);
}

// === Bounded media download ===
async function downloadMediaBounded(msg) {
  const message = unwrapMessage(msg.message);
  const media = message?.imageMessage
    ? { content: message.imageMessage, type: "image" }
    : message?.audioMessage
      ? { content: message.audioMessage, type: "audio" }
      : message?.videoMessage
        ? { content: message.videoMessage, type: "video" }
        : null;

  if (!media) throw new Error("unsupported_media_type");

  const declaredSize = Number(media.content.fileLength || 0);
  if (Number.isFinite(declaredSize) && declaredSize > MAX_MEDIA_BYTES) {
    throw new Error("media_too_large_declared");
  }

  // Unlike downloadMediaMessage(..., "buffer"), this consumes the media stream
  // incrementally and stops retaining chunks as soon as the configured cap is hit.
  const stream = await downloadContentFromMessage(media.content, media.type);
  return collectMediaStreamBounded(stream, MAX_MEDIA_BYTES);
}

// === Image validation ===
function isSupportedImageBuffer(buffer, mime) {
  if (!Buffer.isBuffer(buffer)) return false;
  if (mime === "image/jpeg") return buffer.length >= 3 && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff;
  if (mime === "image/png") return buffer.length >= 8 && buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  if (mime === "image/webp") return buffer.length >= 12 && buffer.toString("ascii", 0, 4) === "RIFF" && buffer.toString("ascii", 8, 12) === "WEBP";
  return false;
}

// === IA: call NVIDIA chat completion ===
async function callIA(userText, imageBase64) {
  const apiKey = process.env.NVIDIA_API_KEY;
  if (!apiKey) throw new Error("NVIDIA_API_KEY not set");

  const identity = getIdentity();
  const systemPrompt = `Eres ${identity.name}, ${identity.role}. Respondes en español de forma clara, concisa y útil. No te identifiques como Claude, ChatGPT, GLM. Tu nombre es ${identity.name}.`;

  let messages;

  if (imageBase64) {
    messages = [
      { role: "system", content: systemPrompt },
      {
        role: "user",
        content: [
          { type: "text", text: userText || "¿Qué hay en esta imagen?" },
          { type: "image_url", image_url: { url: imageBase64 } },
        ],
      },
    ];
  } else {
    messages = [
      { role: "system", content: systemPrompt },
      { role: "user", content: userText },
    ];
  }

  const res = await fetch("https://integrate.api.nvidia.com/v1/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: "z-ai/glm-5.3-flash",
      messages,
      temperature: 0.5,
      top_p: 1,
      max_tokens: 1024,
      stream: false,
    }),
    signal: AbortSignal.timeout(60000),
  });

  if (!res.ok) {
    log(`IA provider returned HTTP ${res.status}`);
    throw new Error(`IA HTTP ${res.status}`);
  }

  const data = await res.json();
  return data?.choices?.[0]?.message?.content || "(sin respuesta)";
}

// === ASR: NVIDIA-hosted Riva/NIM transcription ===
// The endpoint is deployment-specific. Do not silently fall back to an old
// NVCF deployment ID: require the operator to configure the verified endpoint.
async function transcribeAudioNvidia(audioBuffer) {
  const apiKey = process.env.NVIDIA_API_KEY;
  const endpoint = process.env.NVIDIA_ASR_ENDPOINT;
  const fail = (code, message) => {
    const error = new Error(message);
    error.code = code;
    throw error;
  };

  if (!apiKey) fail("ASR_NOT_CONFIGURED", "ASR credentials are not configured");
  if (typeof endpoint !== "string" || !endpoint.trim()) {
    fail("ASR_NOT_CONFIGURED", "ASR endpoint is not configured");
  }
  let endpointUrl;
  try {
    endpointUrl = new URL(endpoint);
  } catch {
    fail("ASR_NOT_CONFIGURED", "ASR endpoint is invalid");
  }
  if (endpointUrl.protocol !== "https:") {
    fail("ASR_NOT_CONFIGURED", "ASR endpoint must use HTTPS");
  }
  if (!Buffer.isBuffer(audioBuffer) || audioBuffer.length === 0) {
    fail("ASR_INVALID_AUDIO", "Audio buffer is empty or invalid");
  }
  if (audioBuffer.length > MAX_MEDIA_BYTES) {
    fail("ASR_INVALID_AUDIO", "Audio buffer exceeds configured size limit");
  }
  // WhatsApp voice notes are Ogg containers. Reject mismatched/garbled payloads
  // instead of uploading arbitrary bytes while claiming they are audio/ogg.
  if (audioBuffer.length < 4 || audioBuffer.toString("ascii", 0, 4) !== "OggS") {
    fail("ASR_INVALID_AUDIO", "Audio is not a valid Ogg container");
  }

  // NVCF may queue requests before a worker is available. Its documented
  // HTTP invocation API supports NVCF-POLL-SECONDS (up to 300 seconds).
  // Keep the client timeout longer than the poll window, and allow operators
  // to tune both without changing code.
  const pollSeconds = Math.min(300, Math.max(1, Number.parseInt(process.env.NVIDIA_ASR_POLL_SECONDS || "120", 10) || 120));
  const timeoutMs = Math.min(300000, Math.max(1000, Number.parseInt(process.env.NVIDIA_ASR_TIMEOUT_MS || "135000", 10) || 135000));

  const form = new FormData();
  form.append("language", "es-US");
  form.append("response_format", "json");
  form.append("file", new Blob([audioBuffer], { type: "audio/ogg" }), "whatsapp-voice-note.ogg");

  let response;
  try {
    response = await fetch(endpointUrl, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "NVCF-POLL-SECONDS": String(pollSeconds),
      },
      body: form,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    const timeout = error?.name === "TimeoutError" || error?.name === "AbortError";
    log(`ASR request failed: ${timeout ? "request_timeout" : "network_error"}`);
    fail(timeout ? "ASR_TIMEOUT" : "ASR_NETWORK_ERROR", "ASR request failed");
  }

  if (!response.ok) {
    log(`ASR provider returned HTTP ${response.status}`);
    fail("ASR_HTTP_ERROR", `ASR provider returned HTTP ${response.status}`);
  }

  let result;
  try {
    result = await response.json();
  } catch {
    log("ASR provider returned invalid JSON");
    fail("ASR_INVALID_RESPONSE", "ASR provider returned invalid JSON");
  }

  const transcript = typeof result?.text === "string" ? result.text.trim() : "";
  if (!transcript) {
    log("ASR provider returned no transcript");
    fail("ASR_EMPTY_TRANSCRIPT", "ASR provider returned no transcript");
  }
  return transcript.slice(0, 12000);
}

// Keep a single explicitly configured provider. A failing or unconfigured
// fallback must not hide the primary ASR error or pretend transcription works.
async function transcribeAudio(audioBuffer) {
  return transcribeAudioNvidia(audioBuffer);
}

// === Message unwrapping (ephemeral, viewOnce, etc.) ===
function unwrapMessage(input) {
  let current = input;
  for (let depth = 0; depth < 5 && current; depth++) {
    const wrapped = current.ephemeralMessage?.message
      || current.viewOnceMessage?.message
      || current.viewOnceMessageV2?.message
      || current.viewOnceMessageV2Extension?.message
      || current.documentWithCaptionMessage?.message;
    if (!wrapped) break;
    current = wrapped;
  }
  return current;
}

// === Message handler ===
async function handleMessage(msg) {
  const msgId = msg.key?.id;
  if (!msgId) return;
  const remoteJid = msg.key?.remoteJid || "";

  // Dedup: skip if already processed recently (now persistent across restarts)
  if (await isDuplicate(remoteJid, msgId)) {
    log(`Duplicate message skipped: ${msgId.slice(0, 8)}`);
    return;
  }

  const message = unwrapMessage(msg.message);
  if (!message) return;
  const from = msg.key.remoteJid;
  const isGroup = from.endsWith("@g.us");
  if (isGroup) return;
  if (msg.key.fromMe) return;

  let text = "";
  let mediaType = "";
  let mediaBuffer = null;

  // Extract text
  if (message.conversation) {
    text = message.conversation;
  } else if (message.extendedTextMessage?.text) {
    text = message.extendedTextMessage.text;
  } else if (message.imageMessage?.caption) {
    text = message.imageMessage.caption;
  } else if (message.audioMessage?.caption) {
    text = message.audioMessage.caption;
  }

  // Extract audio/voice note
  if (message.audioMessage || message.voiceMessage) {
    mediaType = "audio";
    try {
      mediaBuffer = await withMediaSlot(() => downloadMediaBounded(msg));
      log(`Audio media received from ${sanitizeJid(from)}`);
    } catch (e) {
      log(`Audio download failed: ${e.message}`);
      mediaBuffer = null;
    }
  }

  // Extract image
  if (message.imageMessage) {
    mediaType = "image";
    try {
      const mime = String(message.imageMessage?.mimetype || "image/jpeg").toLowerCase();
      if (!["image/jpeg", "image/png", "image/webp"].includes(mime)) {
        throw new Error("unsupported_image_mime");
      }
      mediaBuffer = await withMediaSlot(() => downloadMediaBounded(msg));
      if (!isSupportedImageBuffer(mediaBuffer, mime)) {
        mediaBuffer = null;
        throw new Error("invalid_image_signature");
      }
      log(`Image media received from ${sanitizeJid(from)}`);
    } catch (e) {
      log(`Image download failed: ${e.message}`);
      mediaBuffer = null;
    }
  }

  // Extract video/sticker (can't process yet)
  if (message.videoMessage || message.stickerMessage) {
    mediaType = "unsupported";
  }

  // Keep audio download failures on the normal error-response path.
  if (!text && !mediaBuffer && mediaType !== "unsupported" && mediaType !== "audio") return;

  log(`Processing inbound: type=${mediaType || "text"}, from=${sanitizeJid(from)}`);

  try {
    let reply = "";

    if (mediaType === "audio" && !mediaBuffer) {
      const error = new Error("Audio download failed");
      error.code = "ASR_DOWNLOAD_FAILED";
      throw error;
    }

    if (mediaType === "audio" && mediaBuffer) {
      const transcribed = await transcribeAudio(mediaBuffer);
      if (transcribed) {
        reply = await callIA(transcribed);
      }
    } else if (mediaType === "image" && mediaBuffer) {
      const mime = String(message.imageMessage?.mimetype || "image/jpeg").toLowerCase();
      const base64 = `data:${mime};base64,${mediaBuffer.toString("base64")}`;
      reply = await callIA(text || "¿Qué hay en esta imagen?", base64);
    } else if (mediaType === "unsupported") {
      reply = "📱 Recibí tu mensaje pero aún no puedo procesar videos o stickers. Envíame texto, audio o una imagen.";
    } else if (text) {
      reply = await callIA(text);
    } else {
      return;
    }

    log("Reply generated; sending outbound message");
    await sock.sendMessage(from, { text: reply });
  } catch (err) {
    const code = err?.code || "";
    log(`Message processing failed: ${code.startsWith("ASR_") ? code : "processing_error"}`);
    const userMessage = code.startsWith("ASR_")
      ? "🎤 Recibí tu nota de voz, pero el servicio de transcripción no pudo procesarla. Intenta de nuevo más tarde o envíame el contenido por texto."
      : "⚠️ Ocurrió un error procesando tu mensaje. Intenta de nuevo.";
    try {
      await sock.sendMessage(from, { text: userMessage });
    } catch {}
  }
}

// === Reconnection (OpenClaw-adapted policy) ===
// Adapted from OpenClaw reconnect.ts + connection-controller.ts:
//   - initialMs 2s, maxMs 30s, factor 1.8, jitter 0.25, maxAttempts 12
//   - Reset reconnectAttempts to 0 if the connection lived longer than 1 heartbeat
//   - Distinguish logged_out (no retry) from transient close (retry)
function scheduleReconnect(reason) {
  if (isShuttingDown || reconnectTimer) return;
  if (reconnectAttempts >= RECONNECT_POLICY.maxAttempts) {
    log(`Max reconnection attempts (${RECONNECT_POLICY.maxAttempts}) reached. Manual intervention required.`);
    connectionState = "max_reconnects_exhausted";
    return;
  }

  reconnectAttempts++;
  // OpenClaw computeBackoff: base = min(maxMs, initialMs * factor^(attempt-1))
  //                       final = base - random(0, base * jitter)
  const delay = computeBackoff(RECONNECT_POLICY, reconnectAttempts);

  log(`Reconnect #${reconnectAttempts} in ${(delay / 1000).toFixed(1)}s (reason: ${reason})`);

  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    startWhatsApp().catch((e) => {
      log(`Reconnect failed: ${e.message}`);
      scheduleReconnect("reconnect_error");
    });
  }, delay);
}

// === WhatsApp connection ===
async function startWhatsApp() {
  if (isShuttingDown) throw new Error("daemon_shutting_down");
  if (startPromise) return startPromise;
  if (sock && ["connecting", "linking", "connected"].includes(connectionState)) return;

  const generation = ++connectionGeneration;
  startPromise = (async () => {
    connectionState = "connecting";
    if (sock) {
      const previousSock = sock;
      log("Socket already exists, cleaning up before reconnect");
      try { previousSock.ev.removeAllListeners(); } catch {}
      try { previousSock.end(); } catch {}
      if (sock === previousSock) sock = null;
    }

    // Stop any previous watchdog before opening a new socket.
    if (watchdog) { watchdog.stop(); watchdog = null; }

    // === OpenClaw-adapted session setup ===
    // 1. Ensure session directory exists with mode 0o700 (persistent, not /tmp)
    await ensureSessionDir(SESSION_DIR);

    // 2. Restore creds from backup if main creds.json is corrupt
    const restored = await restoreCredsFromBackupIfNeeded(SESSION_DIR);
    if (restored) {
      log("Restored creds.json from backup (main file was corrupt)");
    }

    // 3. Acquire cross-process file lock (prevents concurrent daemons on same session)
    try {
      connectionOwnerLease = await acquireConnectionOwnerLock(SESSION_DIR);
    } catch (err) {
      if (err instanceof WhatsAppConnectionOwnerBusyError) {
        log(`Another process owns this session: ${err.message}`);
        connectionState = "owner_busy";
        throw err;
      }
      throw err;
    }

    // 4. Dedup store is already initialized at module load (see line above
    //    `dedupStore = createPersistentDedupStore(SESSION_DIR)`). Nothing
    //    to do here — the store is reused across reconnects.

    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);

    // === Hermes-adapted: bound fetchLatestBaileysVersion with 15s timeout ===
    // fetchLatestBaileysVersion() has NO AbortSignal and can hang forever,
    // silently wedging the bridge. Race it against a timeout and cache the
    // last-good version for fallback.
    let version = null;
    try {
      const versionResult = await Promise.race([
        fetchLatestBaileysVersion(),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("version fetch timed out")), 15000)
        ),
      ]);
      version = versionResult.version;
      cachedBaileysVersion = version; // cache for fallback on next failure
    } catch (err) {
      log(`fetchLatestBaileysVersion failed (${err.message}), using fallback`);
      version = cachedBaileysVersion || null; // null = Baileys uses its own default
    }
    if (isShuttingDown) throw new Error("daemon_shutting_down");
    if (generation !== connectionGeneration) throw new Error("connection_start_cancelled");

    // === OpenClaw-adapted: socket options that prevent QR drops ===
    // These options are CRITICAL for connection stability:
    //   - keepAliveIntervalMs: 25s (WhatsApp server idle timeout is ~60s;
    //     Baileys default 30s is borderline → silent connection death → 408)
    //   - connectTimeoutMs: 60s (give user time to scan QR; default 10s is too short)
    //   - defaultQueryTimeoutMs: 60s (queries don't fail mid-handshake)
    //   - syncFullHistory: false (default true → heavy traffic → rate limited → 408)
    //   - markOnlineOnConnect: false (default true → presence spam → rate limited)
    //   - browser: custom identity avoids Baileys default fingerprint
    const currentSock = makeWASocket({
      version: version || undefined,
      auth: state,
      printQRInTerminal: false,
      logger,
      browser: ["AGENTE-GENERAL", "Chrome", "1.0.0"],
      // OpenClaw socket-timing.ts defaults
      keepAliveIntervalMs: 25_000,
      connectTimeoutMs: 60_000,
      defaultQueryTimeoutMs: 60_000,
      // OpenClaw session.ts: prevent rate-limit triggers
      syncFullHistory: false,
      markOnlineOnConnect: false,
      // Baileys 7.x: required for E2EE retry handshake (Hermes pattern)
      getMessage: async () => ({ conversation: "" }),
    });
    sock = currentSock;

    // === OpenClaw session.ts:374-378 — CRITICAL: trap ws error events ===
    // Node's EventEmitter throws on unhandled 'error' events. A raw ws error
    // from the underlying socket, if not subscribed, CRASHES the process →
    // on restart, creds may not be flushed → new QR required.
    if (currentSock.ws && typeof currentSock.ws.on === "function") {
      currentSock.ws.on("error", (err) => {
        log(`WebSocket error: ${String(err).slice(0, 120)}`);
      });
    }

    // === OpenClaw creds-persistence.ts: enqueue atomic creds save ===
    // Direct saveCreds() in creds.update can corrupt creds.json if writes
    // overlap. Use atomic write with .bak backup (already in openclaw-adapted.js).
    currentSock.ev.on("creds.update", async () => {
      try {
        // saveCreds() is Baileys' own function that writes to the auth state dir.
        // We call it directly — the atomic write protection in
        // writeCredsAtomically() applies to our own writes, but Baileys'
        // useMultiFileAuthState already handles its own atomicity internally
        // via temp files. We just need to ensure we don't lose the update.
        await saveCreds();
      } catch (err) {
        log(`creds.update save failed: ${err.message.slice(0, 80)}`);
      }
    });

    currentSock.ev.on("messages.upsert", async (m) => {
      if (sock !== currentSock || isShuttingDown) return;
      const messages = m.messages || [];
      for (const msg of messages) {
        // Note inbound activity for the watchdog (transport + app level)
        watchdog?.noteInbound();
        handleMessage(msg).catch((e) => log(`Handler error: ${e.message}`));
      }
    });

    currentSock.ev.on("connection.update", (update) => {
      // Late events from a replaced socket must never mutate the current session.
      if (sock !== currentSock || isShuttingDown) return;
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        lastQR = qr;
        connectionState = "linking";
        connectedPhone = null;
        log("QR generated, waiting for scan");
      }

      if (connection === "open") {
        connectionState = "connected";
        lastQR = null;
        reconnectAttempts = 0;
        connectionStartTime = Date.now(); // OpenClaw: track for backoff reset
        const user = currentSock.user;
        connectedPhone = user ? user.id.split(":")[0] : null;
        log(`Connected: ${connectedPhone ? "****" : "unknown"}`);

        // Start dual watchdog (transport-level WS frames + app-level inbound msgs)
        // Adapted from OpenClaw connection-controller.ts startTimers():
        //   - 4× grace window post-reconnect (openedAfterRecentInbound)
        //   - transportTimeoutMs: 5 min with no WS frames = dead socket
        //   - messageTimeoutMs: 30 min with no inbound = stale session
        watchdog = createWatchdog(currentSock, {
          openedAfterRecentInbound: reconnectAttempts > 0,
          onTimeout: (info) => {
            log(`Watchdog timeout (${info.reason}): transport=${info.transportStaleForMs}ms app=${info.appSilentForMs}ms msgs=${info.handledMessages}`);
            // Force-close the socket so scheduleReconnect kicks in
            try { currentSock.ev.removeAllListeners(); } catch {}
            try { currentSock.end(); } catch {}
            if (sock === currentSock) sock = null;
            connectionState = "disconnected";
            scheduleReconnect(`watchdog_${info.reason}`);
          },
        });
      }

      if (connection === "close") {
        connectionState = "disconnected";
        connectedPhone = null;

        if (watchdog) { watchdog.stop(); watchdog = null; }
        try { currentSock.ev.removeAllListeners(); } catch {}
        try { currentSock.end(); } catch {}
        if (sock === currentSock) sock = null;

        // === OpenClaw connection-controller.ts:316-372 — status code discrimination ===
        // This is THE most important fix for QR dropping constantly.
        //
        // Baileys wraps disconnect reasons as Boom errors: lastDisconnect.error.output.statusCode
        // Without proper handling, every close → new QR. OpenClaw distinguishes:
        //   - 401 (loggedOut): session invalidated → must rescan QR (no retry)
        //   - 515 (post-pairing restart): WhatsApp asks for restart RIGHT AFTER successful
        //      QR scan. Naive handlers show new QR → user thinks scan failed → loop.
        //      Fix: recreate socket WITHOUT clearing QR (creds from scan are saved).
        //   - 408 (timeout): transient → normal reconnect with backoff
        //   - 499 (watchdog): our own force-close → normal reconnect
        //   - other: transient → normal reconnect
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const isLoggedOut = statusCode === DisconnectReason.loggedOut;
        const isPostPairingRestart = statusCode === 515;
        const isTimeout = statusCode === 408;

        if (isLoggedOut) {
          log("Logged out (401) — session invalidated, QR rescan required");
          connectionState = "logged_out";
          lastQR = null;
          // Don't schedule reconnect — user must scan QR again
        } else if (isPostPairingRestart) {
          // === OpenClaw: 515 handling — recreate socket WITHOUT new QR ===
          // WhatsApp returns 515 right after a successful QR scan. The creds
          // are already saved. We just need to recreate the socket.
          // DO NOT clear lastQR — the user already scanned it successfully.
          log("Post-pairing restart (515) — recreating socket with existing creds");
          // Reset reconnect attempts because this is a normal WhatsApp behavior,
          // not a connection failure
          reconnectAttempts = 0;
          // Schedule a quick reconnect (1s, like Hermes)
          if (reconnectTimer) clearTimeout(reconnectTimer);
          reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            startWhatsApp().catch((e) => {
              log(`515 reconnect failed: ${e.message}`);
              scheduleReconnect("post_pairing_reconnect_error");
            });
          }, 1000);
        } else {
          // Normal close (408 timeout, 499 watchdog, or unknown) → clear QR
          // and schedule reconnect with backoff
          lastQR = null;
          // Reset backoff counter if connection lived longer than 1 heartbeat (60s).
          // OpenClaw connection-controller.ts:720-723 — prevents accumulating
          // backoff on long-lived sockets that eventually hits maxAttempts.
          if (connectionStartTime && Date.now() - connectionStartTime > 60_000) {
            reconnectAttempts = 0;
            log("Connection lived >60s, resetting reconnect counter");
          }
          scheduleReconnect(`connection_close (code=${statusCode})`);
        }
      }
    });
  })();

  try {
    await startPromise;
  } catch (error) {
    // A failed startup must not leave /connect reporting a phantom in-progress session.
    if (generation === connectionGeneration && !sock && connectionState === "connecting") {
      connectionState = "disconnected";
    }
    throw error;
  } finally {
    startPromise = null;
  }
}

// === HTTP server ===
function checkAuth(req) {
  const auth = req.headers.authorization || "";
  const expected = `Bearer ${SECRET}`;
  const suppliedBytes = Buffer.from(auth);
  const expectedBytes = Buffer.from(expected);
  return suppliedBytes.length === expectedBytes.length && timingSafeEqual(suppliedBytes, expectedBytes);
}

function normalizeRecipient(to) {
  if (typeof to !== "string" || !/^\+?\d{7,15}(?:@s\.whatsapp\.net)?$/.test(to)) return null;
  const number = to.replace(/^\+/, "").replace(/@s\.whatsapp\.net$/, "");
  return number + "@s.whatsapp.net";
}

function sendJSON(res, code, data) {
  res.writeHead(code, { "Content-Type": "application/json" });
  res.end(JSON.stringify(data));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    let size = 0;
    let tooLarge = false;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > 64 * 1024) {
        tooLarge = true;
        body = "";
        return;
      }
      if (!tooLarge) body += chunk.toString("utf8");
    });
    req.on("end", () => {
      if (tooLarge) return reject(new Error("BODY_TOO_LARGE"));
      try { resolve(JSON.parse(body)); } catch { reject(new Error("INVALID_JSON")); }
    });
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  if (!checkAuth(req)) {
    return sendJSON(res, 401, { error: "Unauthorized" });
  }

  const url = new URL(req.url, `http://localhost:${PORT}`);
  const path = url.pathname;
  const method = req.method;

  // This daemon is a server-to-server API, not a browser API. Do not emit wildcard CORS headers.
  if (method === "OPTIONS") return sendJSON(res, 405, { error: "Method not allowed" });

  try {
    if (path === "/status" && method === "GET") {
      // Health endpoint: distinguishes daemon_active, connected, disconnected, logged_out, max_reconnects
      return sendJSON(res, 200, {
        status: connectionState,
        phone: connectedPhone ? "****" : null, // Never expose phone
        qr: connectionState === "linking" ? lastQR : null,
        reconnect_attempts: reconnectAttempts,
      });
    }

    if (path === "/qr" && method === "GET") {
      if (!lastQR) return sendJSON(res, 200, { qr: null, message: "No QR. Call /connect first." });
      const qrImage = await QRCode.toDataURL(lastQR, { width: 256 });
      return sendJSON(res, 200, { qr: qrImage });
    }

    if (path === "/connect" && method === "POST") {
      if (connectionState === "connected") return sendJSON(res, 200, { ok: true, message: "Already connected" });
      if (startPromise || ["connecting", "linking"].includes(connectionState)) {
        return sendJSON(res, 200, { ok: true, message: "Connection already in progress; check /qr" });
      }
      reconnectAttempts = 0; // Reset on manual connect
      await startWhatsApp();
      return sendJSON(res, 200, { ok: true, message: "Connecting... check /qr" });
    }

    if (path === "/disconnect" && method === "POST") {
      // Invalidate any asynchronous startup so it cannot create a socket after this response.
      connectionGeneration++;
      const pendingStart = startPromise;
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
      if (pendingStart) {
        try { await pendingStart; } catch {}
      }
      if (sock) {
        const currentSock = sock;
        try { currentSock.ev.removeAllListeners(); } catch {}
        try { await currentSock.logout(); } catch {}
        try { currentSock.end(); } catch {}
        if (sock === currentSock) sock = null;
      }
      connectionState = "disconnected"; connectedPhone = null; lastQR = null;
      return sendJSON(res, 200, { ok: true, message: "Disconnected" });
    }

    if (path === "/send" && method === "POST") {
      const body = await readBody(req);
      if (!body || typeof body !== "object" || Array.isArray(body)) {
        return sendJSON(res, 400, { error: "Invalid request body" });
      }
      if (!sock || connectionState !== "connected") return sendJSON(res, 400, { error: "Not connected" });
      const { to, message } = body;
      if (typeof to !== "string" || typeof message !== "string" || !to || !message.trim()) {
        return sendJSON(res, 400, { error: "A valid recipient and non-empty message are required" });
      }
      if (message.length > 4000) return sendJSON(res, 413, { error: "Message too long" });
      // Only allow individual phone-number JIDs; do not let this endpoint target arbitrary group JIDs.
      const jid = normalizeRecipient(to);
      if (!jid) return sendJSON(res, 400, { error: "Recipient must be an individual phone number" });
      const result = await sock.sendMessage(jid, { text: message.trim() });
      return sendJSON(res, 200, { ok: true, id: result.key.id });
    }

    return sendJSON(res, 404, { error: `Unknown: ${method} ${path}` });
  } catch (err) {
    if (err.message === "BODY_TOO_LARGE") return sendJSON(res, 413, { error: "Request body too large" });
    if (err.message === "INVALID_JSON") return sendJSON(res, 400, { error: "Invalid JSON" });
    log("Request handler failed");
    return sendJSON(res, 500, { error: "Internal error" });
  }
});

server.listen(PORT, HOST, () => {
  log(`WhatsApp daemon listening on ${HOST}:${PORT}`);
  log("Internal authentication configured");
  log(`Limits: media=${MAX_MEDIA_BYTES} bytes, concurrent=${MAX_CONCURRENT_MEDIA}, reconnect_max=${RECONNECT_POLICY.maxAttempts}`);

  // Create session dir with restrictive permissions
  try { fs.mkdirSync(SESSION_DIR, { recursive: true, mode: 0o700 }); } catch {}

  if (fs.existsSync(SESSION_DIR)) {
    log("Found existing session, auto-connecting...");
    startWhatsApp().catch((e) => log(`Auto-connect failed: ${e.message}`));
  }
});

// === Graceful shutdown ===
function shutdown(signal) {
  if (isShuttingDown) return;
  isShuttingDown = true;
  connectionGeneration++;
  log(`Received ${signal}, shutting down...`);

  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }

  // Reject queued work; only remove temporary files created by this process.
  for (const waiter of mediaQueue.splice(0)) {
    waiter.reject(new Error("daemon_shutting_down"));
  }
  for (const tmpFile of ownedTempFiles) {
    try { fs.unlinkSync(tmpFile); } catch {}
    ownedTempFiles.delete(tmpFile);
  }

  if (sock) {
    try { sock.ev.removeAllListeners(); } catch {}
    try { sock.end(); } catch {}
    sock = null;
  }

  // Let active HTTP requests finish. Force exit only as a last resort.
  const forceExit = setTimeout(() => process.exit(1), 5000);
  forceExit.unref();
  server.close(() => {
    clearTimeout(forceExit);
    process.exit(0);
  });
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

module.exports = {
  isDuplicate,
  isSupportedImageBuffer,
  unwrapMessage,
  downloadMediaBounded,
  collectMediaStreamBounded,
  transcribeAudio,
  withMediaSlot,
  scheduleReconnect,
  sanitizeJid,
  checkAuth,
  normalizeRecipient,
  MAX_MEDIA_BYTES,
  MAX_CONCURRENT_MEDIA,
  // OpenClaw-adapted constants (kept for backward compat with existing tests)
  MAX_RECONNECT_ATTEMPTS: RECONNECT_POLICY.maxAttempts,
  // DEDUP_TTL_MS is now defined in openclaw-adapted.js (7 days, persistent).
  // Re-exported here for backward compat with existing tests.
  DEDUP_TTL_MS: require("./openclaw-adapted").DEDUP_TTL_MS,
  MAX_MEDIA_QUEUE,
  // New exports (OpenClaw-adapted)
  RECONNECT_POLICY,
  resolveSessionDir,
  ensureSessionDir,
  acquireConnectionOwnerLock,
  WhatsAppConnectionOwnerBusyError,
  createPersistentDedupStore,
  createWatchdog,
};
