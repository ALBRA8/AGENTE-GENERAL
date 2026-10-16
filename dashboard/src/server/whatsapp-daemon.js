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

// Load .env.local manually
const envPath = path.join(__dirname, "..", "..", ".env.local");
try {
  const envContent = fs.readFileSync(envPath, "utf-8");
  for (const line of envContent.split("\n")) {
    const match = line.match(/^([A-Z_]+)=(.+)$/);
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
const SECRET = process.env.INTERNAL_API_SECRET;
const MAX_MEDIA_BYTES = Math.max(1, parseInt(process.env.MAX_MEDIA_BYTES || (8 * 1024 * 1024).toString(), 10) || (8 * 1024 * 1024));
const MAX_CONCURRENT_MEDIA = Math.max(1, parseInt(process.env.MAX_CONCURRENT_MEDIA || "2", 10) || 2);
const MAX_MEDIA_QUEUE = Math.max(0, parseInt(process.env.MAX_MEDIA_QUEUE || "20", 10) || 0);
const MAX_RECONNECT_ATTEMPTS = parseInt(process.env.MAX_RECONNECT_ATTEMPTS || "10", 10);
const RECONNECT_BASE_DELAY = 1000; // 1s
const RECONNECT_MAX_DELAY = 30000; // 30s
const DEDUP_TTL_MS = 60000; // 1 minute
const SESSION_DIR = "/tmp/wa-session";

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

// Dedup: Set of recently-processed message IDs with TTL
const processedMessages = new Map(); // msgId -> timestamp

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

// === Dedup ===
function isDuplicate(msgId) {
  const now = Date.now();
  // Clean expired entries
  for (const [id, ts] of processedMessages) {
    if (now - ts > DEDUP_TTL_MS) processedMessages.delete(id);
  }
  if (processedMessages.has(msgId)) return true;
  processedMessages.set(msgId, now);
  return false;
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

// === ASR: transcribe audio using z-ai CLI ===
async function transcribeAudio(audioBuffer) {
  const { execFileSync } = require("child_process");
  const tmpFile = `/tmp/wa-audio-${process.pid}-${Date.now()}.ogg`;
  try {
    fs.writeFileSync(tmpFile, audioBuffer, { flag: "wx", mode: 0o600 });
    ownedTempFiles.add(tmpFile);
    const result = execFileSync("z-ai", ["asr", "-f", tmpFile, "--output", "json"], {
      timeout: 30000,
      maxBuffer: 1024 * 1024,
      stdio: ["ignore", "pipe", "ignore"],
    });
    const parsed = JSON.parse(result.toString());
    return typeof parsed.text === "string" ? parsed.text.slice(0, 12000) : "";
  } catch {
    log("ASR processing failed");
    return "";
  } finally {
    try { fs.unlinkSync(tmpFile); } catch {}
    ownedTempFiles.delete(tmpFile);
  }
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

  // Dedup: skip if already processed recently
  if (isDuplicate(msgId)) {
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

  // If nothing to process
  if (!text && !mediaBuffer && mediaType !== "unsupported") return;

  log(`Processing inbound: type=${mediaType || "text"}, from=${sanitizeJid(from)}`);

  try {
    let reply = "";

    if (mediaType === "audio" && mediaBuffer) {
      const transcribed = await transcribeAudio(mediaBuffer);
      if (transcribed) {
        reply = await callIA(transcribed);
      } else {
        reply = "🎤 Recibí tu nota de voz pero no pude transcribirla. ¿Puedes escribirlo?";
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
    log(`Message processing failed: ${err.message}`);
    try {
      await sock.sendMessage(from, { text: "⚠️ Ocurrió un error procesando tu mensaje. Intenta de nuevo." });
    } catch {}
  }
}

// === Reconnection with exponential backoff + jitter ===
function scheduleReconnect(reason) {
  if (isShuttingDown || reconnectTimer) return;
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    log(`Max reconnection attempts (${MAX_RECONNECT_ATTEMPTS}) reached. Manual intervention required.`);
    connectionState = "max_reconnects_exhausted";
    return;
  }

  reconnectAttempts++;
  const baseDelay = Math.min(RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts - 1), RECONNECT_MAX_DELAY);
  const jitter = Math.floor(Math.random() * 1000); // 0-999ms jitter
  const delay = baseDelay + jitter;

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

    const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
    const { version } = await fetchLatestBaileysVersion();
    if (isShuttingDown) throw new Error("daemon_shutting_down");
    if (generation !== connectionGeneration) throw new Error("connection_start_cancelled");

    const currentSock = makeWASocket({
      version,
      auth: state,
      printQRInTerminal: false,
      logger,
      browser: ["AGENTE-GENERAL", "Chrome", "1.0.0"],
    });
    sock = currentSock;

    currentSock.ev.on("creds.update", saveCreds);

    currentSock.ev.on("messages.upsert", async (m) => {
      if (sock !== currentSock || isShuttingDown) return;
      const messages = m.messages || [];
      for (const msg of messages) {
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
        const user = currentSock.user;
        connectedPhone = user ? user.id.split(":")[0] : null;
        log(`Connected: ${connectedPhone ? "****" : "unknown"}`);
      }

      if (connection === "close") {
        connectionState = "disconnected";
        lastQR = null;
        connectedPhone = null;

        try { currentSock.ev.removeAllListeners(); } catch {}
        try { currentSock.end(); } catch {}
        if (sock === currentSock) sock = null;

        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const isLoggedOut = statusCode === DisconnectReason.loggedOut;

        if (isLoggedOut) {
          log("Logged out — session invalidated, QR rescan required");
          connectionState = "logged_out";
        } else {
          scheduleReconnect(`connection_close (code=${statusCode})`);
        }
      }
    });
  })();

  try {
    await startPromise;
  } finally {
    startPromise = null;
  }
}

// === HTTP server ===
function checkAuth(req) {
  const auth = req.headers.authorization || "";
  return auth === `Bearer ${SECRET}`;
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

  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  if (method === "OPTIONS") { res.writeHead(204); return res.end(); }

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
      if (!sock || connectionState !== "connected") return sendJSON(res, 400, { error: "Not connected" });
      const { to, message } = body;
      if (!to || !message) return sendJSON(res, 400, { error: "Missing 'to' or 'message'" });
      const jid = to.includes("@") ? to : `${to}@s.whatsapp.net`;
      const result = await sock.sendMessage(jid, { text: message });
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

server.listen(PORT, () => {
  log(`WhatsApp daemon listening on port ${PORT}`);
  log("Internal authentication configured");
  log(`Limits: media=${MAX_MEDIA_BYTES} bytes, concurrent=${MAX_CONCURRENT_MEDIA}, reconnect_max=${MAX_RECONNECT_ATTEMPTS}`);

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
  withMediaSlot,
  scheduleReconnect,
  sanitizeJid,
  MAX_MEDIA_BYTES,
  MAX_CONCURRENT_MEDIA,
  MAX_RECONNECT_ATTEMPTS,
  DEDUP_TTL_MS,
  MAX_MEDIA_QUEUE,
};
