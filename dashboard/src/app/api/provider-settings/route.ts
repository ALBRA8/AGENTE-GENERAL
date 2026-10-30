import { NextResponse } from "next/server";
import { readFile, writeFile } from "fs/promises";
import path from "path";

const SETTINGS_FILE = path.join(process.cwd(), ".provider-settings.json");
const ENV_LOCAL_FILE = path.join(process.cwd(), ".env.local");

const DEFAULT_SETTINGS = {
  provider: "groq",
  base_url: "https://api.groq.com/openai/v1",
  model: "openai/gpt-oss-120b",
  api_key: process.env.GROQ_API_KEY || "",
  temperature: 0.5,
  top_p: 1.0,
  max_tokens: 1024,
  timeout: 60,
};

export async function GET() {
  try {
    const data = await readFile(SETTINGS_FILE, "utf-8");
    const json = JSON.parse(data);
    return NextResponse.json({ ...DEFAULT_SETTINGS, ...json });
  } catch {
    // If not found, check environment
    const current = {
      provider: process.env.GROQ_API_KEY ? "groq" : process.env.NVIDIA_API_KEY ? "nvidia" : "mock",
      base_url: process.env.GROQ_API_KEY ? "https://api.groq.com/openai/v1" : "https://integrate.api.nvidia.com/v1",
      model: process.env.GROQ_API_KEY ? "openai/gpt-oss-120b" : "z-ai/glm-5.3-flash",
      api_key: process.env.GROQ_API_KEY || process.env.NVIDIA_API_KEY || "",
      temperature: 0.5,
      top_p: 1.0,
      max_tokens: 1024,
      timeout: 60,
    };
    return NextResponse.json(current);
  }
}

export async function PUT(request: Request) {
  try {
    const body = await request.json();
    const provider = body.provider || "groq";
    const baseUrl = body.base_url || "https://api.groq.com/openai/v1";
    const model = body.model || "openai/gpt-oss-120b";
    const apiKey = body.api_key || "";
    const temperature = Number(body.temperature) || 0.5;
    const topP = Number(body.top_p) || 1.0;
    const maxTokens = Number(body.max_tokens) || 1024;
    const timeout = Number(body.timeout) || 60;

    const toSave = {
      provider,
      base_url: baseUrl,
      model,
      api_key: apiKey,
      temperature,
      top_p: topP,
      max_tokens: maxTokens,
      timeout,
    };

    // Save to settings file
    await writeFile(SETTINGS_FILE, JSON.stringify(toSave, null, 2), "utf-8");

    // Also update .env.local dynamically
    try {
      const envContent = await readFile(ENV_LOCAL_FILE, "utf-8").catch(() => "");
      const lines = envContent.split("\n");
      const envMap: Record<string, string> = {};
      for (const line of lines) {
        const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
        if (m) envMap[m[1]] = m[2];
      }

      if (provider === "groq") {
        envMap["GROQ_API_KEY"] = apiKey;
        process.env.GROQ_API_KEY = apiKey;
      } else if (provider === "nvidia") {
        envMap["NVIDIA_API_KEY"] = apiKey;
        process.env.NVIDIA_API_KEY = apiKey;
      } else if (provider === "google") {
        envMap["GOOGLE_API_KEY"] = apiKey;
        process.env.GOOGLE_API_KEY = apiKey;
      } else if (provider === "openai") {
        envMap["OPENAI_API_KEY"] = apiKey;
        process.env.OPENAI_API_KEY = apiKey;
      }

      envMap["AI_PROVIDER"] = provider;
      envMap["AI_BASE_URL"] = baseUrl;
      envMap["AI_MODEL"] = model;

      const newEnv = Object.entries(envMap).map(([k, v]) => `${k}=${v}`).join("\n") + "\n";
      await writeFile(ENV_LOCAL_FILE, newEnv, "utf-8");
    } catch {}

    return NextResponse.json({ ok: true, config: toSave });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: err.message || "Failed to save" }, { status: 500 });
  }
}
