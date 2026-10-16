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

  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const messages = body.messages;
  if (!messages || !Array.isArray(messages) || messages.length === 0) {
    return NextResponse.json({ error: "messages array required" }, { status: 400 });
  }

  // Get saved identity (or use request override)
  const saved = await getIdentity();
  const agentName = body.agent_name || saved.name || "AGENTE GENERAL";
  const agentRole = body.agent_persona || `${saved.role || "asistente de IA"}. Respondes en español de forma clara, concisa y útil. Eres capaz de conversar, responder preguntas y ayudar con tareas.`;

  // Inject system prompt if not present
  const hasSystem = messages.some((m: any) => m.role === "system");
  const systemContent = `Eres ${agentName}, ${agentRole} No te identifiques como Claude, ChatGPT, GLM, ni ningún otro modelo. Tu nombre es ${agentName}.`;
  const systemPrompt = { role: "system", content: systemContent };
  const finalMessages = hasSystem ? messages : [systemPrompt, ...messages];

  const temperature = body.temperature ?? 0.5;
  const maxTokens = body.max_tokens ?? 1024;

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
      const text = await res.text().catch(() => "unknown error");
      const safe = text.replace(/nvapi-[A-Za-z0-9_\-.]{8,}/g, "nvapi-[REDACTED]");
      return NextResponse.json(
        { error: `NVIDIA API error: HTTP ${res.status}`, detail: safe.slice(0, 200) },
        { status: res.status }
      );
    }

    const data = await res.json();
    const content = data?.choices?.[0]?.message?.content || "(sin respuesta)";

    return NextResponse.json({ content, model: data?.model || NVIDIA_MODEL, agent_name: agentName });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "unknown error";
    return NextResponse.json(
      { error: `Request failed: ${msg}` },
      { status: 500 }
    );
  }
}
