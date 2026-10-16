/**
 * POST /api/chat — proxy to NVIDIA NIM (OpenAI-compatible)
 *
 * The API key lives in .env.local (server-side only).
 * The browser NEVER sees the key.
 *
 * Request body: {
 *   messages: [{role, content}],
 *   temperature?,
 *   max_tokens?,
 *   agent_name? — custom name for the agent (default: "AGENTE GENERAL")
 *   agent_persona? — custom persona description (optional)
 * }
 * Response: { content: "..." }
 */

import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import path from "path";

const NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1";
const NVIDIA_MODEL = "z-ai/glm-5.3-flash";
const IDENTITY_FILE = path.join(process.cwd(), ".agent-identity.json");

async function getIdentity() {
  try {
    const data = await readFile(IDENTITY_FILE, "utf-8");
    return JSON.parse(data);
  } catch {
    return { name: "AGENTE GENERAL", role: "asistente de IA" };
  }
}

export async function POST(request: Request) {
  const apiKey = process.env.NVIDIA_API_KEY;

  if (!apiKey) {
    return NextResponse.json(
      { error: "NVIDIA_API_KEY not configured in .env.local" },
      { status: 500 }
    );
  }

  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (declaredLength > 12 * 1024 * 1024) return NextResponse.json({ error: "Request body too large" }, { status: 413 });
  let body;
  try {
    const rawBody = await request.text();
    if (Buffer.byteLength(rawBody, "utf8") > 12 * 1024 * 1024) return NextResponse.json({ error: "Request body too large" }, { status: 413 });
    body = JSON.parse(rawBody);
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return NextResponse.json({ error: "Invalid request body" }, { status: 400 });

  const messages = body.messages;
  if (!messages || !Array.isArray(messages) || messages.length === 0 || messages.length > 100) {
    return NextResponse.json({ error: "messages array required" }, { status: 400 });
  }

  // Get saved identity (or use request override)
  const saved = await getIdentity();
  const agentName = body.agent_name || saved.name || "AGENTE GENERAL";
  const agentRole = body.agent_persona || `${saved.role || "asistente de IA"}. Respondes en español de forma clara, concisa y útil. Eres capaz de conversar, responder preguntas y ayudar con tareas.`;

  // Inject system prompt if not present
  const hasSystem = messages.some((m: { role?: string } | null) => m?.role === "system");
  const systemContent = `Eres ${agentName}, ${agentRole} No te identifiques como Claude, ChatGPT, GLM, ni ningún otro modelo. Tu nombre es ${agentName}.`;
  const systemPrompt = { role: "system", content: systemContent };

  const temperature = body.temperature ?? 0.5;
  const maxTokens = body.max_tokens ?? 1024;

  // Handle image attachment (multimodal)
  const image = body.image;
  let finalMessages;

  if (image) {
    if (typeof image !== "string" || image.length > 11 * 1024 * 1024 || !/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/.test(image)) {
      return NextResponse.json({ error: "Image must be a PNG, JPEG, or WebP base64 data URL under 8 MiB" }, { status: 400 });
    }
    const imagePayload = image.slice(image.indexOf(",") + 1);
    if (Buffer.from(imagePayload, "base64").length > 8 * 1024 * 1024) return NextResponse.json({ error: "Image too large" }, { status: 413 });
    // User sent an image — build multimodal message
    const userText = messages[messages.length - 1]?.content || "¿Qué hay en esta imagen?";
    finalMessages = [
      { role: "system", content: systemContent },
      {
        role: "user",
        content: [
          { type: "text", text: userText },
          { type: "image_url", image_url: { url: image } },
        ],
      },
    ];
  } else if (body.audio) {
    // Validate and transcribe with the installed z-ai ASR CLI.
    const fs = await import("fs/promises");
    const { execFileSync } = await import("child_process");
    const audioMatch = typeof body.audio === "string" && body.audio.match(/^data:(audio\/(?:webm|ogg|mpeg|wav|mp4|x-wav));base64,([A-Za-z0-9+/]+={0,2})$/);
    if (audioMatch) {
      const audioMime = audioMatch[1];
      const audioBase64 = audioMatch[2];
      const audioExtension: Record<string, string> = { "audio/webm": "webm", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/wav": "wav", "audio/mp4": "m4a", "audio/x-wav": "wav" };
      const buf = Buffer.from(audioBase64, "base64");
      if (!buf.length || buf.length > 8 * 1024 * 1024) return NextResponse.json({ error: "Audio too large or empty" }, { status: 413 });
      const tmpFile = `/tmp/chat-audio-${process.pid}-${Date.now()}.${audioExtension[audioMime] || "audio"}`;
      try {
        await fs.writeFile(tmpFile, buf, { flag: "wx", mode: 0o600 });
        const result = execFileSync("z-ai", ["asr", "-f", tmpFile, "-o", "/dev/stdout"], {
          timeout: 30000,
          maxBuffer: 1024 * 1024,
          stdio: ["ignore", "pipe", "ignore"],
        }).toString().trim();
        let transcribed = "";
        try {
          const parsed = JSON.parse(result);
          transcribed = typeof parsed.text === "string" ? parsed.text.slice(0, 12000) : "";
        } catch {
          transcribed = result.slice(0, 12000);
        }
        if (transcribed) {
          finalMessages = [
            { role: "system", content: systemContent },
            { role: "user", content: `El usuario dijo (transcripción de audio): "${transcribed}". Responde al contenido del audio.` },
          ];
        } else {
          return NextResponse.json({ content: "🎤 No pude transcribir el audio. ¿Puedes escribirlo?" });
        }
      } catch {
        return NextResponse.json({ content: "🎤 No pude procesar el audio. ¿Puedes escribirlo?" });
      } finally {
        await fs.unlink(tmpFile).catch(() => {});
      }
    } else {
      return NextResponse.json({ error: "Invalid audio format" }, { status: 400 });
    }
  } else {
    // Normal text
    finalMessages = hasSystem ? messages : [systemPrompt, ...messages];
  }

  try {
    const res = await fetch(`${NVIDIA_BASE_URL}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model: NVIDIA_MODEL,
        messages: finalMessages,
        temperature,
        top_p: 1,
        max_tokens: maxTokens,
        stream: false,
      }),
      signal: AbortSignal.timeout(60000),
    });

    if (!res.ok) {
      return NextResponse.json(
        { error: `NVIDIA API error: HTTP ${res.status}` },
        { status: res.status >= 400 && res.status < 600 ? res.status : 502 }
      );
    }

    const data = await res.json();
    const content = data?.choices?.[0]?.message?.content || "(sin respuesta)";

    return NextResponse.json({ content, model: data?.model || NVIDIA_MODEL, agent_name: agentName });
  } catch (e) {
    return NextResponse.json(
      { error: "Request failed while contacting the configured model provider" },
      { status: 500 }
    );
  }
}
