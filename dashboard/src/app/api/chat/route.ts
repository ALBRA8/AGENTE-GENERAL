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
  const groqKey = process.env.GROQ_API_KEY;
  const nvidiaKey = process.env.NVIDIA_API_KEY;
  const openaiKey = process.env.OPENAI_API_KEY;

  if (!groqKey && !nvidiaKey && !openaiKey) {
    return NextResponse.json(
      { error: "No AI provider key configured in .env.local (GROQ_API_KEY or NVIDIA_API_KEY required)" },
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
    const imageBytes = Buffer.from(imagePayload, "base64");
    if (imageBytes.length > 8 * 1024 * 1024) return NextResponse.json({ error: "Image too large" }, { status: 413 });
    const imageMime = image.slice(5, image.indexOf(";")).toLowerCase();
    const validSignature =
      imageMime === "image/jpeg" ? imageBytes.length >= 3 && imageBytes[0] === 0xff && imageBytes[1] === 0xd8 && imageBytes[2] === 0xff :
      imageMime === "image/png" ? imageBytes.length >= 8 && imageBytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) :
      imageMime === "image/webp" ? imageBytes.length >= 12 && imageBytes.toString("ascii", 0, 4) === "RIFF" && imageBytes.toString("ascii", 8, 12) === "WEBP" : false;
    if (!validSignature) return NextResponse.json({ error: "Image content does not match its declared type" }, { status: 400 });
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
    // Use the configured NVIDIA ASR HTTP endpoint rather than a local CLI.
    const audioMatch = typeof body.audio === "string"
      && body.audio.match(/^data:(audio\/(?:webm|ogg|mpeg|wav|mp4|x-wav));base64,([A-Za-z0-9+/]+={0,2})$/);
    if (!audioMatch) {
      return NextResponse.json({ error: "Invalid audio format" }, { status: 400 });
    }

    const audioMime = audioMatch[1];
    const audioBytes = Buffer.from(audioMatch[2], "base64");
    if (!audioBytes.length) {
      return NextResponse.json({ error: "Audio is empty" }, { status: 400 });
    }
    if (audioBytes.length > 8 * 1024 * 1024) {
      return NextResponse.json({ error: "Audio too large" }, { status: 413 });
    }

    const audioExtension: Record<string, string> = {
      "audio/webm": "webm",
      "audio/ogg": "ogg",
      "audio/mpeg": "mp3",
      "audio/wav": "wav",
      "audio/mp4": "m4a",
      "audio/x-wav": "wav",
    };

    let transcribed = "";

    // 1. Try Groq Whisper first (ultra-fast, ~500ms)
    if (groqKey) {
      try {
        const form = new FormData();
        form.append("language", "es");
        form.append("model", "whisper-large-v3-turbo");
        form.append(
          "file",
          new Blob([audioBytes], { type: audioMime }),
          `chat-audio.${audioExtension[audioMime] || "ogg"}`,
        );

        const groqAsr = await fetch("https://api.groq.com/openai/v1/audio/transcriptions", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${groqKey}`,
          },
          body: form,
          signal: AbortSignal.timeout(30000),
        });

        if (groqAsr.ok) {
          const asrData = await groqAsr.json();
          if (typeof asrData?.text === "string" && asrData.text.trim()) {
            transcribed = asrData.text.trim().slice(0, 12000);
          }
        }
      } catch {}
    }

    // 2. Fallback to NVIDIA ASR if configured
    if (!transcribed && process.env.NVIDIA_ASR_ENDPOINT && process.env.NVIDIA_ASR_ENDPOINT.trim()) {
      const asrEndpoint = process.env.NVIDIA_ASR_ENDPOINT;
      try {
        const asrUrl = new URL(asrEndpoint);
        if (asrUrl.protocol === "https:") {
          const form = new FormData();
          form.append("language", "es-US");
          form.append("response_format", "json");
          form.append(
            "file",
            new Blob([audioBytes], { type: audioMime }),
            `chat-audio.${audioExtension[audioMime] || "ogg"}`,
          );

          const pollSeconds = Math.min(300, Math.max(1, Number.parseInt(process.env.NVIDIA_ASR_POLL_SECONDS || "120", 10) || 120));
          const timeoutMs = Math.min(300000, Math.max(1000, Number.parseInt(process.env.NVIDIA_ASR_TIMEOUT_MS || "135000", 10) || 135000));
          const asrKey = nvidiaKey || groqKey || openaiKey || "";
          const asrResponse = await fetch(asrUrl, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${asrKey}`,
              "NVCF-POLL-SECONDS": String(pollSeconds),
            },
            body: form,
            signal: AbortSignal.timeout(timeoutMs),
          });
          if (asrResponse.ok) {
            const asrResult = await asrResponse.json().catch(() => ({}));
            if (typeof asrResult?.text === "string" && asrResult.text.trim()) {
              transcribed = asrResult.text.trim().slice(0, 12000);
            }
          }
        }
      } catch {}
    }

    if (!transcribed) {
      return NextResponse.json({ content: "🎤 No pude transcribir el audio. Por favor verifica que tu clave de Groq u OpenAI esté activa." });
    }

    finalMessages = [
      { role: "system", content: systemContent },
      { role: "user", content: `El usuario dijo (transcripción de nota de voz): "${transcribed}". Responde a lo que dijo de forma útil y atenta.` },
    ];
  } else {
    // Normal text
    finalMessages = hasSystem ? messages : [systemPrompt, ...messages];
  }

  // 1. Try Groq first for high-speed response
  if (groqKey) {
    try {
      const targetModel = image ? "qwen/qwen3.8-27b" : "openai/gpt-oss-120b";
      const res = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${groqKey}`,
        },
        body: JSON.stringify({
          model: targetModel,
          messages: finalMessages,
          temperature,
          max_tokens: maxTokens,
          stream: false,
        }),
        signal: AbortSignal.timeout(30000),
      });

      if (res.ok) {
        const data = await res.json();
        const msg = data?.choices?.[0]?.message;
        const content = msg?.content || msg?.reasoning_content || "(sin respuesta)";
        return NextResponse.json({ content, model: data?.model || targetModel, agent_name: agentName });
      }
    } catch {
      // Fall through to NVIDIA if available
    }
  }

  // 2. Fallback to NVIDIA NIM
  const targetNvidiaKey = nvidiaKey || openaiKey;
  if (targetNvidiaKey) {
    try {
      const res = await fetch(`${NVIDIA_BASE_URL}/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${targetNvidiaKey}`,
        },
        body: JSON.stringify({
          model: NVIDIA_MODEL,
          messages: finalMessages,
          temperature,
          top_p: 1,
          max_tokens: Math.max(maxTokens, 2048),
          stream: false,
        }),
        signal: AbortSignal.timeout(90000),
      });

      if (!res.ok) {
        return NextResponse.json(
          { error: `Provider API error: HTTP ${res.status}` },
          { status: res.status >= 400 && res.status < 600 ? res.status : 502 }
        );
      }

      const data = await res.json();
      const msg = data?.choices?.[0]?.message;
      const content = msg?.content || msg?.reasoning_content || "(sin respuesta)";

      return NextResponse.json({ content, model: data?.model || NVIDIA_MODEL, agent_name: agentName });
    } catch {
      return NextResponse.json(
        { error: "Request failed while contacting the configured model provider" },
        { status: 500 }
      );
    }
  }

  return NextResponse.json({ error: "No operational model provider available" }, { status: 500 });
}
