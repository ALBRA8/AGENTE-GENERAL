"use client";

import { Header } from "@/components/header";
import { useEffect, useState } from "react";
import { loadProviders } from "@/lib/providers";

interface BackendStatus {
  reachable: boolean;
  phase: string;
  ready: boolean;
  agent_id: string;
  agent_name: string;
  error?: string;
}

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export default function Home() {
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [providerCount, setProviderCount] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setProviderCount(loadProviders().length);

    let cancelled = false;
    const run = async () => {
      try {
        const healthRes = await fetch(`${BACKEND_URL}/api/v1/health`, {
          signal: AbortSignal.timeout(5000),
        });
        if (!healthRes.ok) throw new Error(`HTTP ${healthRes.status}`);
        const health = await healthRes.json();

        const readyRes = await fetch(`${BACKEND_URL}/api/v1/readiness`, {
          signal: AbortSignal.timeout(5000),
        });
        const ready = readyRes.ok
          ? await readyRes.json()
          : { ready: false, phase: "unknown" };

        const agentRes = await fetch(`${BACKEND_URL}/api/v1/agent/info`, {
          signal: AbortSignal.timeout(5000),
        });
        const agent = agentRes.ok
          ? await agentRes.json()
          : { agent_id: "unknown", name: "unknown" };

        if (cancelled) return;
        setStatus({
          reachable: true,
          phase: health.phase || "unknown",
          ready: Boolean(ready.ready),
          agent_id: agent.agent_id || "unknown",
          agent_name: agent.name || "unknown",
        });
      } catch (err) {
        if (cancelled) return;
        setStatus({
          reachable: false,
          phase: "unreachable",
          ready: false,
          agent_id: "—",
          agent_name: "—",
          error: err instanceof Error ? err.message : "error desconocido",
        });
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <Header />
      <main style={{ maxWidth: "64rem", margin: "0 auto", padding: "2rem 1rem" }}>
        <h1
          style={{
            fontSize: "1.5rem",
            fontWeight: 600,
            margin: "0 0 0.25rem",
          }}
        >
          Resumen
        </h1>
        <p style={{ color: "var(--muted-fg)", fontSize: "0.875rem", margin: "0 0 2rem" }}>
          Estado del agente y de los proveedores configurados.
        </p>

        <div
          style={{
            display: "grid",
            gap: "1rem",
            gridTemplateColumns: "repeat(auto-fit, minmax(16rem, 1fr))",
          }}
        >
          {/* Backend status card */}
          <Card>
            <CardTitle>Backend</CardTitle>
            {loading ? (
              <CardValue>—</CardValue>
            ) : status?.reachable ? (
              <>
                <CardValue>
                  <StatusDot ok={status.ready} />
                  {status.ready ? "Listo" : "Degradado"}
                </CardValue>
                <CardMeta>
                  Fase: {status.phase}
                  <br />
                  Agente: {status.agent_name} ({status.agent_id})
                </CardMeta>
              </>
            ) : (
              <>
                <CardValue>
                  <StatusDot ok={false} />
                  No disponible
                </CardValue>
                <CardMeta>
                  {status?.error || "El backend no responde."}
                  <br />
                  URL: {BACKEND_URL}
                </CardMeta>
              </>
            )}
          </Card>

          {/* Providers configured card */}
          <Card>
            <CardTitle>Proveedores</CardTitle>
            <CardValue>{providerCount}</CardValue>
            <CardMeta>
              {providerCount === 0
                ? "Ninguno configurado. Ve a Configuración."
                : `${providerCount} proveedor(es) guardado(s) en este navegador.`}
            </CardMeta>
          </Card>

          {/* Quick links card */}
          <Card>
            <CardTitle>Accesos rápidos</CardTitle>
            <div style={{ display: "grid", gap: "0.5rem", marginTop: "0.5rem" }}>
              <QuickLink href="/chat" label="Probar chat con el proveedor activo" />
              <QuickLink href="/config" label="Configurar proveedores de API" />
            </div>
          </Card>
        </div>
      </main>
    </>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: "0.75rem",
        padding: "1.25rem",
      }}
    >
      {children}
    </div>
  );
}

function CardTitle({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: "0.75rem",
        fontWeight: 500,
        textTransform: "uppercase",
        letterSpacing: "0.05em",
        color: "var(--muted-fg)",
        marginBottom: "0.5rem",
      }}
    >
      {children}
    </div>
  );
}

function CardValue({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: "1.5rem",
        fontWeight: 600,
        display: "flex",
        alignItems: "center",
        gap: "0.5rem",
      }}
    >
      {children}
    </div>
  );
}

function CardMeta({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontSize: "0.75rem",
        color: "var(--muted-fg)",
        marginTop: "0.5rem",
        lineHeight: 1.5,
      }}
    >
      {children}
    </div>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: "0.5rem",
        height: "0.5rem",
        borderRadius: "9999px",
        background: ok ? "#10b981" : "#ef4444",
      }}
    />
  );
}

function QuickLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      style={{
        display: "block",
        padding: "0.5rem 0.75rem",
        borderRadius: "0.375rem",
        fontSize: "0.875rem",
        background: "var(--muted)",
        color: "var(--fg)",
        textDecoration: "none",
      }}
    >
      {label} →
    </a>
  );
}
