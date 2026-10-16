"use client";

import { useEffect, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "https://agente-general.onrender.com";
const DEFAULT_ALLOWED_API_ORIGINS = ["https://agente-general.onrender.com"];
const ALLOWED_API_ORIGINS = (
  process.env.NEXT_PUBLIC_ALLOWED_API_ORIGINS ||
  DEFAULT_ALLOWED_API_ORIGINS.join(",")
)
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean)
  .map((origin) => new URL(origin).origin);

function assertSecureApiBase(): void {
  const parsed = new URL(API_BASE);
  const localHosts = new Set(["localhost", "127.0.0.1", "[::1]"]);
  const isLocalHttp =
    parsed.protocol === "http:" && localHosts.has(parsed.hostname);
  const isLocalDevelopment = localHosts.has(parsed.hostname);
  if (
    (parsed.protocol !== "https:" && !isLocalHttp) ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    (!isLocalDevelopment && !ALLOWED_API_ORIGINS.includes(parsed.origin))
  ) {
    throw new Error(
      "La API debe usar un origen HTTPS autorizado (HTTP solo en localhost), sin credenciales ni query."
    );
  }
}

interface ProviderConfig {
  provider: string;
  base_url: string;
  model: string;
  temperature: number;
  top_p: number;
  max_tokens: number;
  timeout: number;
  stream: boolean;
  api_key_set: boolean;
  last_test_at: string | null;
  last_test_ok: boolean | null;
}

interface DefaultsResponse {
  mock: Record<string, unknown>;
  nvidia: Record<string, unknown>;
}

export default function Home() {
  const [config, setConfig] = useState<ProviderConfig | null>(null);
  const [defaults, setDefaults] = useState<DefaultsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showApiKey, setShowApiKey] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [dark, setDark] = useState(false);
  // Kept in component memory only; never persisted to localStorage.
  const [accessToken, setAccessToken] = useState("");

  const authHeaders = (): Record<string, string> => {
    // Never send the administrator bearer token or provider API key over
    // cleartext HTTP to a non-local host or to a malformed API base URL.
    assertSecureApiBase();
    return accessToken.trim()
      ? { Authorization: `Bearer ${accessToken.trim()}` }
      : {};
  };

  // Form state
  const [provider, setProvider] = useState("mock");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [temperature, setTemperature] = useState(0.5);
  const [topP, setTopP] = useState(1.0);
  const [maxTokens, setMaxTokens] = useState(1024);
  const [timeout, setTimeoutVal] = useState(60);
  const [stream, setStream] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [cfgRes, defRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/provider/config`, { headers: authHeaders() }),
        fetch(`${API_BASE}/api/v1/provider/defaults`, { headers: authHeaders() }),
      ]);
      if (!cfgRes.ok) throw new Error(`HTTP ${cfgRes.status}`);
      const cfg = await cfgRes.json();
      setConfig(cfg);
      setProvider(cfg.provider || "mock");
      setBaseUrl(cfg.base_url || "");
      setModel(cfg.model || "");
      setTemperature(cfg.temperature ?? 0.5);
      setTopP(cfg.top_p ?? 1.0);
      setMaxTokens(cfg.max_tokens ?? 1024);
      setTimeoutVal(cfg.timeout ?? 60);
      setStream(cfg.stream ?? false);
      if (defRes.ok) {
        setDefaults(await defRes.json());
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error desconocido");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    if (next) {
      document.documentElement.classList.add("dark");
      localStorage.setItem("theme", "dark");
    } else {
      document.documentElement.classList.remove("dark");
      localStorage.setItem("theme", "light");
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/provider/config`, {
        method: "PUT",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
          provider,
          base_url: baseUrl,
          model,
          temperature,
          top_p: topP,
          max_tokens: maxTokens,
          timeout,
          stream,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      const cfg = await res.json();
      setConfig(cfg);
      setSuccess("Configuración guardada");

      // If user entered a new API key, save it
      if (apiKey.trim()) {
        const keyRes = await fetch(`${API_BASE}/api/v1/provider/api-key`, {
          method: "PUT",
          headers: { ...authHeaders(), "Content-Type": "application/json" },
          body: JSON.stringify({ api_key: apiKey.trim() }),
        });
        if (!keyRes.ok) {
          const data = await keyRes.json().catch(() => ({}));
          throw new Error(`API key: ${data.detail || keyRes.status}`);
        }
        setApiKey("");
        setSuccess("Configuración + API key guardadas");
      }
      // Reload to get fresh api_key_set
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al guardar");
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/provider/test`, {
        method: "POST",
        headers: authHeaders(),
      });
      const data = await res.json();
      if (data.ok) {
        setSuccess(`Conexión exitosa (${data.latency_ms?.toFixed(0) || "?"}ms)`);
      } else {
        setError(`Conexión fallida: ${data.message || data.detail}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al probar");
    } finally {
      setTesting(false);
    }
  };

  const reset = async () => {
    if (!confirm("¿Restaurar configuración por defecto (Mock)? Se borrará la API key.")) return;
    setSaving(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/provider/reset`, {
        method: "POST",
        headers: authHeaders(),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      await load();
      setSuccess("Configuración restaurada a Mock");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error al restaurar");
    } finally {
      setSaving(false);
    }
  };

  const applyNvidiaDefaults = () => {
    if (defaults?.nvidia) {
      const n = defaults.nvidia as Record<string, unknown>;
      setProvider("nvidia");
      setBaseUrl(n.base_url as string);
      setModel(n.model as string);
      setTemperature(n.temperature as number);
      setTopP(n.top_p as number);
      setMaxTokens(n.max_tokens as number);
      setTimeoutVal(n.timeout as number);
      setStream(n.stream as boolean);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: "2rem", textAlign: "center", color: "var(--muted-fg)" }}>
        Cargando configuración...
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh" }}>
      <header
        style={{
          borderBottom: "1px solid var(--border)",
          padding: "0.75rem 1rem",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <strong>AGENTE GENERAL</strong>
        <button
          onClick={toggleTheme}
          style={{
            background: "var(--muted)",
            border: "1px solid var(--border)",
            borderRadius: "0.5rem",
            padding: "0.4rem 0.8rem",
            cursor: "pointer",
            fontSize: "0.8rem",
          }}
        >
          {dark ? "☀ Claro" : "☾ Oscuro"}
        </button>
      </header>

      <main style={{ maxWidth: "48rem", margin: "0 auto", padding: "2rem 1rem" }}>
        <h1 style={{ fontSize: "1.5rem", fontWeight: 600, marginBottom: "0.5rem" }}>
          Configuración → Agente → Proveedor de IA
        </h1>
        <p style={{ color: "var(--muted-fg)", fontSize: "0.875rem", marginBottom: "2rem" }}>
          Configura el proveedor de LLM. La API key se cifra en el servidor y nunca se muestra ni se envía a terceros.
        </p>

        {/* Access token: intentionally memory-only, never persisted. */}
        <div style={{ marginBottom: "1rem", display: "flex", gap: "0.5rem" }}>
          <input
            type="password"
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
            placeholder="Token de acceso del administrador"
            autoComplete="off"
            aria-label="Token de acceso del administrador"
            style={{ ...inputStyle, flex: 1 }}
          />
          <button onClick={load} disabled={loading || !accessToken.trim()} style={secondaryBtn}>
            {loading ? "Conectando..." : "Conectar"}
          </button>
        </div>

        {/* Status badge */}
        {config && (
          <div
            style={{
              display: "flex",
              gap: "1rem",
              marginBottom: "1.5rem",
              flexWrap: "wrap",
            }}
          >
            <StatusBadge
              label="Configuración"
              ok={config.provider !== "mock" || config.api_key_set}
              text={config.provider === "mock" ? "Mock (sin configurar)" : `${config.provider} configurado`}
            />
            <StatusBadge
              label="Proveedor accesible"
              ok={config.last_test_ok === true}
              text={
                config.last_test_ok === true
                  ? "Última prueba OK"
                  : config.last_test_ok === false
                    ? "Última prueba fallida"
                    : "Sin probar"
              }
            />
            <StatusBadge
              label="API key"
              ok={config.api_key_set}
              text={config.api_key_set ? "Credencial configurada" : "Sin credencial"}
            />
          </div>
        )}

        {error && (
          <div
            style={{
              background: "rgba(239,68,68,0.1)",
              color: "var(--danger)",
              padding: "0.75rem 1rem",
              borderRadius: "0.5rem",
              fontSize: "0.875rem",
              marginBottom: "1rem",
            }}
          >
            {error}
          </div>
        )}
        {success && (
          <div
            style={{
              background: "rgba(16,185,129,0.1)",
              color: "var(--success)",
              padding: "0.75rem 1rem",
              borderRadius: "0.5rem",
              fontSize: "0.875rem",
              marginBottom: "1rem",
            }}
          >
            {success}
          </div>
        )}

        {/* Form */}
        <div
          style={{
            border: "1px solid var(--border)",
            borderRadius: "0.75rem",
            padding: "1.5rem",
            background: "var(--card)",
          }}
        >
          {/* Provider selector */}
          <div style={{ marginBottom: "1rem" }}>
            <label style={labelStyle}>Proveedor</label>
            <select
              value={provider}
              onChange={(e) => {
                setProvider(e.target.value);
                if (e.target.value === "nvidia") applyNvidiaDefaults();
              }}
              style={inputStyle}
            >
              <option value="mock">Mock (offline, sin credenciales)</option>
              <option value="nvidia">NVIDIA NIM (OpenAI-compatible)</option>
            </select>
          </div>

          {/* Base URL */}
          <div style={{ marginBottom: "1rem" }}>
            <label style={labelStyle}>Base URL</label>
            <input
              type="url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://integrate.api.nvidia.com/v1"
              style={inputStyle}
              disabled={provider === "mock"}
            />
          </div>

          {/* Model */}
          <div style={{ marginBottom: "1rem" }}>
            <label style={labelStyle}>Modelo</label>
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="z-ai/glm-5.3"
              style={inputStyle}
              disabled={provider === "mock"}
            />
          </div>

          {/* API Key */}
          <div style={{ marginBottom: "1rem" }}>
            <label style={labelStyle}>
              API Key {config?.api_key_set && "(credencial ya configurada — dejar vacío para mantener)"}
            </label>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <input
                type={showApiKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={config?.api_key_set ? "•••••••• (dejar vacío para mantener)" : "nvapi-..."}
                style={{ ...inputStyle, flex: 1 }}
                disabled={provider === "mock"}
                autoComplete="off"
              />
              <button
                onClick={() => setShowApiKey(!showApiKey)}
                style={smallBtn}
                disabled={provider === "mock"}
              >
                {showApiKey ? "Ocultar" : "Mostrar"}
              </button>
            </div>
          </div>

          {/* Temperature + top_p */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginBottom: "1rem" }}>
            <div>
              <label style={labelStyle}>Temperatura: {temperature.toFixed(2)}</label>
              <input
                type="range"
                min="0"
                max="2"
                step="0.05"
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
                style={{ width: "100%" }}
              />
            </div>
            <div>
              <label style={labelStyle}>top_p: {topP.toFixed(2)}</label>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={topP}
                onChange={(e) => setTopP(parseFloat(e.target.value))}
                style={{ width: "100%" }}
              />
            </div>
          </div>

          {/* max_tokens + timeout */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem", marginBottom: "1rem" }}>
            <div>
              <label style={labelStyle}>Máximo de tokens</label>
              <input
                type="number"
                min="1"
                max="32768"
                value={maxTokens}
                onChange={(e) => setMaxTokens(parseInt(e.target.value) || 1024)}
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>Timeout (segundos)</label>
              <input
                type="number"
                min="1"
                max="600"
                value={timeout}
                onChange={(e) => setTimeoutVal(parseInt(e.target.value) || 60)}
                style={inputStyle}
              />
            </div>
          </div>

          {/* Stream */}
          <div style={{ marginBottom: "1.5rem" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={stream}
                onChange={(e) => setStream(e.target.checked)}
                disabled={provider === "mock"}
              />
              <span style={{ fontSize: "0.875rem" }}>Modo streaming (experimental)</span>
            </label>
          </div>

          {/* Last test info */}
          {config?.last_test_at && (
            <div style={{ fontSize: "0.75rem", color: "var(--muted-fg)", marginBottom: "1rem" }}>
              Último test: {new Date(config.last_test_at).toLocaleString("es-ES")} —{" "}
              {config.last_test_ok ? "✓ OK" : "✗ Fallido"}
            </div>
          )}

          {/* Buttons */}
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            <button
              onClick={save}
              disabled={saving}
              style={{
                ...primaryBtn,
                opacity: saving ? 0.5 : 1,
              }}
            >
              {saving ? "Guardando..." : "Guardar"}
            </button>
            <button
              onClick={test}
              disabled={testing}
              style={{
                ...secondaryBtn,
                opacity: testing ? 0.5 : 1,
              }}
            >
              {testing ? "Probando..." : "Probar conexión"}
            </button>
            <button onClick={reset} disabled={saving} style={secondaryBtn}>
              Restaurar defaults
            </button>
          </div>
        </div>

        {/* Info */}
        <div
          style={{
            marginTop: "2rem",
            padding: "1rem",
            background: "var(--muted)",
            borderRadius: "0.5rem",
            fontSize: "0.75rem",
            color: "var(--muted-fg)",
            lineHeight: 1.6,
          }}
        >
          <strong>Seguridad:</strong> La API key se cifra con Fernet en el servidor
          usando <code>SETTINGS_ENCRYPTION_KEY</code>. No se almacena en localStorage,
          sessionStorage ni en código del frontend. No se devuelve en ningún endpoint
          de lectura. Si la dejas vacía al guardar, se conserva la credencial anterior.
        </div>
      </main>
    </div>
  );
}

function StatusBadge({ label, ok, text }: { label: string; ok: boolean; text: string }) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "0.5rem",
        padding: "0.5rem 0.75rem",
        background: "var(--card)",
        fontSize: "0.75rem",
      }}
    >
      <div style={{ color: "var(--muted-fg)", marginBottom: "0.25rem" }}>{label}</div>
      <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontWeight: 500 }}>
        <span
          style={{
            width: "0.5rem",
            height: "0.5rem",
            borderRadius: "9999px",
            background: ok ? "var(--success)" : "var(--muted-fg)",
            flexShrink: 0,
          }}
        />
        {text}
      </div>
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "0.75rem",
  fontWeight: 500,
  color: "var(--muted-fg)",
  marginBottom: "0.25rem",
};

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  background: "var(--bg)",
  color: "var(--fg)",
  border: "1px solid var(--border)",
  borderRadius: "0.375rem",
  padding: "0.5rem 0.75rem",
  fontSize: "0.875rem",
};

const primaryBtn: React.CSSProperties = {
  background: "var(--accent)",
  color: "var(--accent-fg)",
  border: "1px solid var(--accent)",
  borderRadius: "0.5rem",
  padding: "0.5rem 1.25rem",
  fontSize: "0.875rem",
  fontWeight: 500,
  cursor: "pointer",
};

const secondaryBtn: React.CSSProperties = {
  background: "transparent",
  color: "var(--fg)",
  border: "1px solid var(--border)",
  borderRadius: "0.5rem",
  padding: "0.5rem 1rem",
  fontSize: "0.875rem",
  cursor: "pointer",
};

const smallBtn: React.CSSProperties = {
  background: "var(--muted)",
  color: "var(--fg)",
  border: "1px solid var(--border)",
  borderRadius: "0.375rem",
  padding: "0.25rem 0.6rem",
  fontSize: "0.75rem",
  cursor: "pointer",
};
