/**
 * WhatsApp Daemon — Baileys-based WhatsApp Web server.
 *
 * Runs on port 3002. Generates QR codes for linking WhatsApp Web.
 * The Next.js dashboard proxies to this daemon via /api/whatsapp/daemon-proxy.
 *
 * Endpoints:
 *   GET  /status     — { status, phone, orgLinked }
 *   GET  /qr         — { qr: "data:image/png;base64,..." }
 *   POST /connect     — starts QR generation
 *   POST /disconnect  — closes WhatsApp session
 *   POST /send        — sends a message
 *
 * Security:
 *   - Bearer token (INTERNAL_API_SECRET env var)
 *   - No secrets logged
 *   - Session data stored in /tmp/wa-session (ephemeral)
 */

const http = require("http");
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require("@whiskeysockets/baileys");
const QRCode = require("qrcode");
const P = require("pino");

const PORT = process.env.WHATSAPP_DAEMON_PORT || 3002;
const SECRET = process.env.INTERNAL_API_SECRET || "dev-secret";
const SESSION_DIR = "/tmp/wa-session";

let sock = null;
let lastQR = null;
let connectionState = "disconnected";
let connectedPhone = null;

const logger = P({ level: "silent" }); // Don't log sensitive data

function log(msg) {
  console.log(`[WA-DAEMON] ${new Date().toISOString()} ${msg}`);
}

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

  // === AUTO-REPLY: incoming messages → IA → reply ===
  sock.ev.on("messages.upsert", async (m) => {
    const messages = m.messages || [];
    for (const msg of messages) {
      // Skip if no text content
      if (!msg.message) continue;
      const from = msg.key.remoteJid;
      const isGroup = from.endsWith("@g.us");
      // Skip group messages (can enable later if needed)
      if (isGroup) continue;

      // Extract text from message
      let text = "";
      if (msg.message.conversation) {
        text = msg.message.conversation;
      } else if (msg.message.extendedTextMessage?.text) {
        text = msg.message.extendedTextMessage.text;
      }

      if (!text || !text.trim()) continue;
      // Skip our own messages
      if (msg.key.fromMe) continue;

      log(`Incoming from ${from}: ${text.slice(0, 60)}...`);

      try {
        // Get agent identity
        const fs = require("fs");
        let agentName = "AGENTE GENERAL";
        let agentRole = "asistente de IA";
        try {
          const idFile = require("path").join(process.cwd(), ".agent-identity.json");
          const idData = JSON.parse(fs.readFileSync(idFile, "utf-8"));
          agentName = idData.name || agentName;
          agentRole = idData.role || agentRole;
        } catch {}

        // Call the NVIDIA API (same as /api/chat but directly)
        const apiKey = process.env.NVIDIA_API_KEY;
        if (!apiKey) {
          log("No NVIDIA_API_KEY — cannot reply");
          await sock.sendMessage(from, { text: "⚠️ El agente no está configurado correctamente." });
          continue;
        }

        const systemPrompt = `Eres ${agentName}, ${agentRole}. Respondes en español de forma clara, concisa y útil. No te identifiques como Claude, ChatGPT, GLM. Tu nombre es ${agentName}.`;

        const aiRes = await fetch("https://integrate.api.nvidia.com/v1/chat/completions", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${apiKey}`,
          },
          body: JSON.stringify({
            model: "z-ai/glm-5.3-flash",
            messages: [
              { role: "system", content: systemPrompt },
              { role: "user", content: text },
            ],
            temperature: 0.5,
            top_p: 1,
            max_tokens: 1024,
            stream: false,
          }),
        });

        if (!aiRes.ok) {
          log(`AI error: HTTP ${aiRes.status}`);
          await sock.sendMessage(from, { text: "⚠️ No pude procesar tu mensaje ahora mismo. Intenta más tarde." });
          continue;
        }

        const aiData = await aiRes.json();
        const reply = aiData?.choices?.[0]?.message?.content || "(sin respuesta)";
        log(`Reply to ${from}: ${reply.slice(0, 60)}...`);
        await sock.sendMessage(from, { text: reply });
      } catch (err) {
        log(`Error processing message: ${err.message}`);
        await sock.sendMessage(from, { text: "⚠️ Ocurrió un error. Intenta más tarde." });
      }
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

  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  if (method === "OPTIONS") {
    res.writeHead(204);
    return res.end();
  }

  try {
    if (path === "/status" && method === "GET") {
      return sendJSON(res, 200, {
        status: connectionState,
        phone: connectedPhone,
        qr: connectionState === "linking" ? lastQR : null,
      });
    }

    if (path === "/qr" && method === "GET") {
      if (!lastQR) {
        return sendJSON(res, 200, { qr: null, message: "No QR available. Call /connect first." });
      }
      // Generate QR as data URL
      const qrImage = await QRCode.toDataURL(lastQR, { width: 256 });
      return sendJSON(res, 200, { qr: qrImage });
    }

    if (path === "/connect" && method === "POST") {
      if (connectionState === "connected") {
        return sendJSON(res, 200, { ok: true, message: "Already connected", phone: connectedPhone });
      }
      await startWhatsApp();
      return sendJSON(res, 200, { ok: true, message: "Connecting... check /qr" });
    }

    if (path === "/disconnect" && method === "POST") {
      if (sock) {
        await sock.logout();
        sock = null;
      }
      connectionState = "disconnected";
      connectedPhone = null;
      lastQR = null;
      return sendJSON(res, 200, { ok: true, message: "Disconnected" });
    }

    if (path === "/send" && method === "POST") {
      const body = await readBody(req);
      if (!sock || connectionState !== "connected") {
        return sendJSON(res, 400, { error: "Not connected" });
      }
      const { to, message } = body;
      if (!to || !message) {
        return sendJSON(res, 400, { error: "Missing 'to' or 'message'" });
      }
      const jid = to.includes("@") ? to : `${to}@s.whatsapp.net`;
      const result = await sock.sendMessage(jid, { text: message });
      return sendJSON(res, 200, { ok: true, id: result.key.id });
    }

    return sendJSON(res, 404, { error: `Unknown path: ${method} ${path}` });
  } catch (err) {
    log(`Error: ${err.message}`);
    return sendJSON(res, 500, { error: "Internal error", message: err.message });
  }
});

server.listen(PORT, () => {
  log(`WhatsApp daemon listening on port ${PORT}`);
  log(`Secret: ${SECRET === "dev-secret" ? "(dev mode)" : "configured"}`);
  // Auto-start if session exists
  const fs = require("fs");
  if (fs.existsSync(SESSION_DIR)) {
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
