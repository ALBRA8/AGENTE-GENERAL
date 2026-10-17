/**
 * Agent identity API — GET/PUT /api/agent-identity
 *
 * Stores the agent name + role in a JSON file on the server.
 * All channels (chat, WhatsApp, Telegram) read this identity.
 *
 * NO secrets stored here — just name and role.
 */

import { NextResponse } from "next/server";
import { readFile, writeFile, mkdir } from "fs/promises";
import path from "path";

const IDENTITY_FILE = path.join(process.cwd(), ".agent-identity.json");

const DEFAULT_IDENTITY = {
  name: "AGENTE GENERAL",
  role: "asistente de IA",
};

export async function GET() {
  try {
    const data = await readFile(IDENTITY_FILE, "utf-8");
    return NextResponse.json(JSON.parse(data));
  } catch {
    return NextResponse.json(DEFAULT_IDENTITY);
  }
}

export async function PUT(request: Request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const name = (body.name || "").trim() || DEFAULT_IDENTITY.name;
  const role = (body.role || "").trim() || DEFAULT_IDENTITY.role;

  const identity = { name, role, updated_at: new Date().toISOString() };

  try {
    await mkdir(path.dirname(IDENTITY_FILE), { recursive: true });
    await writeFile(IDENTITY_FILE, JSON.stringify(identity, null, 2), "utf-8");
    return NextResponse.json({ ok: true, ...identity });
  } catch (e) {
    return NextResponse.json(
      { error: "Failed to save identity" },
      { status: 500 }
    );
  }
}
