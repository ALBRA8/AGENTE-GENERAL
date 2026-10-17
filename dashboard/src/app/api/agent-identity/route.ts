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
  const declaredLength = Number(request.headers.get("content-length") || 0);
  if (declaredLength > 16 * 1024) {
    return NextResponse.json({ error: "Request body too large" }, { status: 413 });
  }

  let body;
  try {
    const raw = await request.text();
    if (Buffer.byteLength(raw, "utf8") > 16 * 1024) {
      return NextResponse.json({ error: "Request body too large" }, { status: 413 });
    }
    body = JSON.parse(raw);
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
  }
  if ((body.name !== undefined && typeof body.name !== "string") ||
      (body.role !== undefined && typeof body.role !== "string")) {
    return NextResponse.json({ error: "Name and role must be strings" }, { status: 400 });
  }

  const name = (body.name || "").trim() || DEFAULT_IDENTITY.name;
  const role = (body.role || "").trim() || DEFAULT_IDENTITY.role;
  if (name.length > 80 || role.length > 500) {
    return NextResponse.json({ error: "Name or role exceeds the allowed length" }, { status: 400 });
  }

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
