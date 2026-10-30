import { NextResponse } from "next/server";

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const provider = body.provider || "groq";
    const baseUrl = body.base_url || (provider === "groq" ? "https://api.groq.com/openai/v1" : "https://integrate.api.nvidia.com/v1");
    const model = body.model || (provider === "groq" ? "openai/gpt-oss-120b" : "z-ai/glm-5.3-flash");
    const apiKey = body.api_key || process.env.GROQ_API_KEY || process.env.NVIDIA_API_KEY;

    if (!apiKey && provider !== "mock") {
      return NextResponse.json({ ok: false, message: "API Key requerida" }, { status: 400 });
    }

    if (provider === "mock") {
      return NextResponse.json({ ok: true, latency_ms: 10, message: "Mock OK" });
    }

    const t0 = performance.now();
    const endpoint = `${baseUrl.replace(/\/+$/, "")}/chat/completions`;
    const res = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({
        model,
        messages: [{ role: "user", content: "ping" }],
        max_tokens: 16,
      }),
      signal: AbortSignal.timeout(20000),
    });

    const latency_ms = performance.now() - t0;

    if (!res.ok) {
      const errText = await res.text().catch(() => "");
      return NextResponse.json({ ok: false, message: `HTTP ${res.status}: ${errText.slice(0, 100)}` }, { status: 200 });
    }

    return NextResponse.json({ ok: true, latency_ms, message: "Conexión exitosa" });
  } catch (err: unknown) {
    return NextResponse.json({ ok: false, message: err.message || "Error al conectar" }, { status: 200 });
  }
}
