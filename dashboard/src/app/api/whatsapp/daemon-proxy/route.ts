/**
 * GET/POST /api/whatsapp/daemon-proxy
 *
 * Proxy to the WhatsApp Baileys daemon on port 3002.
 * The frontend NEVER connects directly to the daemon.
 *
 *   GET  ?path=/status   — connection status
 *   GET  ?path=/qr       — QR code as data URL
 *   POST { path: "/connect" }    — start WhatsApp linking
 *   POST { path: "/disconnect" } — close session
 *   POST { path: "/send", to, message } — send a message
 */

import { NextResponse } from "next/server";

const DAEMON_URL = process.env.WHATSAPP_DAEMON_URL || "http://localhost:3002";
const INTERNAL_API_SECRET = process.env.INTERNAL_API_SECRET || "dev-secret";
const TIMEOUT_MS = 5000;

const ALLOWED_GET = ["/status", "/qr"];
const ALLOWED_POST = ["/connect", "/disconnect", "/send"];

function daemonHeaders() {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${INTERNAL_API_SECRET}`,
  };
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const path = searchParams.get("path") || "/status";

  if (!ALLOWED_GET.includes(path)) {
    return NextResponse.json({ error: `Path not allowed: ${path}` }, { status: 400 });
  }

  try {
    const res = await fetch(`${DAEMON_URL}${path}`, {
      headers: daemonHeaders(),
      signal: AbortSignal.timeout(TIMEOUT_MS),
      cache: "no-store",
    });

    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") || "application/json" },
    });
  } catch {
    return NextResponse.json(
      {
        status: "daemon_offline",
        qr: null,
        phone: null,
        message: "WhatsApp daemon not running. Start it with: npm run wa-daemon",
      },
      { status: 503 }
    );
  }
}

export async function POST(request: Request) {
  let body;
  try {
    body = await request.json();
  } catch {
    body = {};
  }

  const path = body.path || "/connect";

  if (!ALLOWED_POST.includes(path)) {
    return NextResponse.json({ error: `Path not allowed: ${path}` }, { status: 400 });
  }

  try {
    const { path: _path, ...payload } = body;
    const res = await fetch(`${DAEMON_URL}${path}`, {
      method: "POST",
      headers: daemonHeaders(),
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });

    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: { "Content-Type": res.headers.get("content-type") || "application/json" },
    });
  } catch {
    return NextResponse.json(
      {
        ok: false,
        error: "WhatsApp daemon not running. Start it with: npm run wa-daemon",
      },
      { status: 503 }
    );
  }
}
