/**
 * WhatsApp Daemon — Baileys + IA auto-reply (text, audio, images)
 *
 * Runs on port 3002. Responds to incoming WhatsApp messages using NVIDIA IA.
 *
 * Supported message types:
 *   - Text → reply directly
 *   - Audio/voice note → transcribe with z-ai ASR → reply
 *   - Image → describe with z-ai VLM → reply
 *   - Sticker/video → "no puedo procesar esto aún"
 *
 * Security:
 *   - Bearer token (INTERNAL_API_SECRET)
 *   - API keys in .env.local (never logged)
 *   - Session in /tmp/wa-session (ephemeral)
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

const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, downloadMediaMessage } = require("@whiskeysockets/baileys");
const QRCode = require("qrcode");
const P = require("pino");

const PORT = process.env.WHATSAPP_DAEMON_PORT || 3002;
const SECRET = process.env.INTERNAL_API_SECRET;
const MAX_MEDIA_BYTES = 8 * 1024 * 1024;
if (!SECRET || SECRET.length < 32) throw new Error("INTERNAL_API_SECRET must be configured with at least 32 characters");
const SESSION_DIR = "/tmp/wa-session";

let sock = null;
let lastQR = null;
let connectionState = "disconnected";
let connectedPhone = null;

const logger = P({ level: "silent" });

function log(msg) {
  console.log(`[WA-DAEMON] ${new Date().toISOString()} ${msg}`);
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

// === IA: call NVIDIA chat completion ===
async function callIA(userText, imageBase64) {
  const apiKey = process.env.NVIDIA_API_KEY;
  if (!apiKey) throw new Error("NVIDIA_API_KEY not set");

  const identity = getIdentity();
  const systemPrompt = `Eres ${identity.name}, ${identity.role}. Respondes en español de forma clara, concisa y útil. No te identifiques como Claude, ChatGPT, GLM. Tu nombre es ${identity.name}.`;

  // If image, use the dashboard's /api/chat with image support
  // For now, describe the image via NVIDIA then answer
  let messages;

  if (imageBase64) {
    // Send image as a multimodal message (NVIDIA supports OpenAI vision format)
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
    const errText = await res.text().catch(() => "unknown");
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
  }
}

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

function isSupportedImageBuffer(buffer, mime) {
  if (!Buffer.isBuffer(buffer)) return false;
  if (mime === "image/jpeg") return buffer.length >= 3 && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff;
  if (mime === "image/png") return buffer.length >= 8 && buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  if (mime === "image/webp") return buffer.length >= 12 && buffer.toString("ascii", 0, 4) === "RIFF" && buffer.toString("ascii", 8, 12) === "WEBP";
  return false;
}

// === Message handler ===
async function handleMessage(msg) {
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
      const declaredSize = Number(message.audioMessage?.fileLength || message.voiceMessage?.fileLength || 0);
      if (declaredSize > MAX_MEDIA_BYTES) throw new Error("media_too_large");
      mediaBuffer = await downloadMediaMessage(msg, "buffer", {}, logger);
      if (!Buffer.isBuffer(mediaBuffer) || mediaBuffer.length > MAX_MEDIA_BYTES) { mediaBuffer = null; throw new Error("media_too_large"); }
      log("Audio media received");
    } catch (e) {
      log("Audio download failed");
    }
  }

  // Extract image
  if (message.imageMessage) {
    mediaType = "image";
    try {
      const imageMessage = message.imageMessage;
      const declaredSize = Number(imageMessage?.fileLength || 0);
      const mime = String(imageMessage?.mimetype || "image/jpeg").toLowerCase();
      if (declaredSize > MAX_MEDIA_BYTES || !["image/jpeg", "image/png", "image/webp"].includes(mime)) throw new Error("unsupported_image");
      mediaBuffer = await downloadMediaMessage(msg, "buffer", {}, logger);
      if (!Buffer.isBuffer(mediaBuffer) || mediaBuffer.length > MAX_MEDIA_BYTES) { mediaBuffer = null; throw new Error("media_too_large"); }
      if (!isSupportedImageBuffer(mediaBuffer, mime)) { mediaBuffer = null; throw new Error("invalid_image_signature"); }
      log("Image media received");
    } catch (e) {
      log("Image download failed");
    }
  }

  // Extract video/sticker (can't process yet)
  if (message.videoMessage || message.stickerMessage) {
    mediaType = "unsupported";
  }

  // If nothing to process
  if (!text && !mediaBuffer && mediaType !== "unsupported") return;

  log(`Processing inbound message: type=${mediaType || "text"}`);

  try {
    let reply = "";

    if (mediaType === "audio" && mediaBuffer) {
      // Transcribe audio → text → IA
      const transcribed = await transcribeAudio(mediaBuffer);
      if (transcribed) {
        log(`Transcribed: "${transcribed.slice(0, 60)}"`);
        reply = await callIA(transcribed);
      } else {
        reply = "🎤 Recibí tu nota de voz pero no pude transcribirla. ¿Puedes escribirlo?";
      }
    } else if (mediaType === "image" && mediaBuffer) {
      // Convert image to base64 and send to IA with vision
      const mime = String(message.imageMessage?.mimetype || "image/jpeg").toLowerCase();
      const base64 = `data:${mime};base64,${mediaBuffer.toString("base64")}`;
      reply = await callIA(text || "¿Qué hay en esta imagen?", base64);
    } else if (mediaType === "unsupported") {
      reply = "📱 Recibí tu mensaje pero aún no puedo procesar videos o stickers. Envíame texto, audio o una imagen.";
    } else if (text) {
      // Regular text → IA
      reply = await callIA(text);
    } else {
      return; // Nothing to respond to
    }

    log("Reply generated; sending outbound message");
    await sock.sendMessage(from, { text: reply });
  } catch (err) {
    log("Message processing failed");
    await sock.sendMessage(from, { text: "⚠️ Ocurrió un error procesando tu mensaje. Intenta de nuevo." });
  }
}

// === WhatsApp connection ===
async function startWhatsApp() {
  if (sock) {
    log("Already running, disconnect first");
    return;
  }

  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    logger,
    browser: ["AGENTE-GENERAL", "Chrome", "1.0.0"],
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("messages.upsert", async (m) => {
    const messages = m.messages || [];
    for (const msg of messages) {
      await handleMessage(msg);
    }
  });

  sock.ev.on("connection.update", (update) => {
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
      const user = sock.user;
      connectedPhone = user ? user.id.split(":")[0] : null;
      log(`Connected: ${connectedPhone}`);
    }

    if (connection === "close") {
      connectionState = "disconnected";
      lastQR = null;
      connectedPhone = null;
      sock = null;

      const shouldReconnect =
        lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;

      if (shouldReconnect) {
        log("Connection closed, will reconnect...");
        setTimeout(() => startWhatsApp(), 3000);
      } else {
        log("Logged out, session cleared");
      }
    }
  });
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
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > 64 * 1024) {
        reject(new Error("BODY_TOO_LARGE"));
        req.destroy();
        return;
      }
      body += chunk.toString("utf8");
    });
    req.on("end", () => {
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
      return sendJSON(res, 200, { status: connectionState, phone: connectedPhone, qr: connectionState === "linking" ? lastQR : null });
    }

    if (path === "/qr" && method === "GET") {
      if (!lastQR) return sendJSON(res, 200, { qr: null, message: "No QR. Call /connect first." });
      const qrImage = await QRCode.toDataURL(lastQR, { width: 256 });
      return sendJSON(res, 200, { qr: qrImage });
    }

    if (path === "/connect" && method === "POST") {
      if (connectionState === "connected") return sendJSON(res, 200, { ok: true, message: "Already connected", phone: connectedPhone });
      await startWhatsApp();
      return sendJSON(res, 200, { ok: true, message: "Connecting... check /qr" });
    }

    if (path === "/disconnect" && method === "POST") {
      if (sock) { await sock.logout(); sock = null; }
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
  const fs2 = require("fs");
  if (fs2.existsSync(SESSION_DIR)) {
    log("Found existing session, auto-connecting...");
    startWhatsApp().catch((e) => log(`Auto-connect failed: ${e.message}`));
  }
});

process.on("SIGTERM", () => {
  log("Shutting down...");
  if (sock) sock.end();
  server.close();
  process.exit(0);
});
