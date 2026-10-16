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
const SECRET = process.env.INTERNAL_API_SECRET || "dev-secret";
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
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => "unknown");
    log(`IA error: HTTP ${res.status} — ${errText.slice(0, 100)}`);
    throw new Error(`IA HTTP ${res.status}`);
  }

  const data = await res.json();
  return data?.choices?.[0]?.message?.content || "(sin respuesta)";
}

// === ASR: transcribe audio using z-ai CLI ===
async function transcribeAudio(audioBuffer) {
  try {
    const { execSync } = require("child_process");
    const tmpFile = `/tmp/wa-audio-${Date.now()}.ogg`;
    fs.writeFileSync(tmpFile, audioBuffer);
    const result = execSync(`z-ai asr -f "${tmpFile}" --output json 2>/dev/null`, {
      timeout: 30000,
      maxBuffer: 1024 * 1024,
    });
    fs.unlinkSync(tmpFile);
    const parsed = JSON.parse(result.toString());
    return parsed.text || "";
  } catch (e) {
    log(`ASR failed: ${e.message}`);
    return "";
  }
}

// === Message handler ===
async function handleMessage(msg) {
  if (!msg.message) return;
  const from = msg.key.remoteJid;
  const isGroup = from.endsWith("@g.us");
  if (isGroup) return;
  if (msg.key.fromMe) return;

  let text = "";
  let mediaType = "";
  let mediaBuffer = null;

  // Extract text
  if (msg.message.conversation) {
    text = msg.message.conversation;
  } else if (msg.message.extendedTextMessage?.text) {
    text = msg.message.extendedTextMessage.text;
  }

  // Extract audio/voice note
  if (msg.message.audioMessage || msg.message.voiceMessage) {
    mediaType = "audio";
    try {
      mediaBuffer = await downloadMediaMessage(msg, "buffer", {}, logger);
      log(`Audio received from ${from}, size: ${mediaBuffer?.length || 0} bytes`);
    } catch (e) {
      log(`Audio download failed: ${e.message}`);
    }
  }

  // Extract image
  if (msg.message.imageMessage) {
    mediaType = "image";
    try {
      mediaBuffer = await downloadMediaMessage(msg, "buffer", {}, logger);
      log(`Image received from ${from}, size: ${mediaBuffer?.length || 0} bytes`);
    } catch (e) {
      log(`Image download failed: ${e.message}`);
    }
  }

  // Extract video/sticker (can't process yet)
  if (msg.message.videoMessage || msg.message.stickerMessage) {
    mediaType = "unsupported";
  }

  // If nothing to process
  if (!text && !mediaBuffer && mediaType !== "unsupported") return;

  log(`Processing message from ${from}: type=${mediaType || "text"}, text=${text.slice(0, 50)}`);

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
      const base64 = `data:image/jpeg;base64,${mediaBuffer.toString("base64")}`;
      reply = await callIA(text || "¿Qué hay en esta imagen?", base64);
    } else if (mediaType === "unsupported") {
      reply = "📱 Recibí tu mensaje pero aún no puedo procesar videos o stickers. Envíame texto, audio o una imagen.";
    } else if (text) {
      // Regular text → IA
      reply = await callIA(text);
    } else {
      return; // Nothing to respond to
    }

    log(`Reply to ${from}: ${reply.slice(0, 80)}...`);
    await sock.sendMessage(from, { text: reply });
  } catch (err) {
    log(`Error processing: ${err.message}`);
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
  return new Promise((resolve) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      try { resolve(JSON.parse(body)); } catch { resolve({}); }
    });
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
    log(`Error: ${err.message}`);
    return sendJSON(res, 500, { error: "Internal error", message: err.message });
  }
});

server.listen(PORT, () => {
  log(`WhatsApp daemon listening on port ${PORT}`);
  log(`Secret: ${SECRET === "dev-secret" ? "(dev mode)" : "configured"}`);
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
