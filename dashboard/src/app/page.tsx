"use client";

import { useState, useEffect } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "https://agente-general.onrender.com";

type View = "chat" | "config" | "telegram" | "whatsapp";

export default function Home() {
  const [view, setView] = useState<View>("chat");
  const [dark, setDark] = useState(false);

  useEffect(() => {
    if (typeof document !== "undefined") {
      setDark(document.documentElement.classList.contains("dark"));
    }
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

  const navItems: { key: View; label: string; icon: string }[] = [
    { key: "chat", label: "Chat", icon: "💬" },
    { key: "config", label: "Proveedor IA", icon: "⚙️" },
    { key: "telegram", label: "Telegram", icon: "✈️" },
    { key: "whatsapp", label: "WhatsApp", icon: "📱" },
  ];

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <header style={headerStyle}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <strong style={{ fontSize: "0.9rem" }}>🤖 AGENTE GENERAL</strong>
        </div>
        <nav style={{ display: "flex", gap: "0.25rem" }}>
          {navItems.map((item) => (
            <button
              key={item.key}
              onClick={() => setView(item.key)}
              style={{
                ...navBtn,
                background: view === item.key ? "var(--accent)" : "transparent",
                color: view === item.key ? "var(--accent-fg)" : "var(--muted-fg)",
                borderBottom: view === item.key ? "2px solid var(--accent)" : "2px solid transparent",
              }}
            >
              <span style={{ marginRight: "0.3rem" }}>{item.icon}</span>
              <span style={{ fontSize: "0.8rem" }}>{item.label}</span>
            </button>
          ))}
        </nav>
        <button onClick={toggleTheme} style={themeBtn}>
          {dark ? "☀️" : "🌙"}
        </button>
      </header>

      {/* Main content */}
      <main style={{ flex: 1, maxWidth: "56rem", margin: "0 auto", width: "100%", padding: "1rem" }}>
        {view === "chat" && <ChatView />}
        {view === "config" && <ConfigView />}
        {view === "telegram" && <TelegramView />}
        {view === "whatsapp" && <WhatsAppView />}
      </main>
    </div>
  );
}

// =====================================================================
// CHAT VIEW
// =====================================================================

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  pending?: boolean;
  error?: boolean;
}

function ChatView() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Check if backend is reachable
    fetch(`${API_BASE}/api/v1/health`)
      .then((r) => r.ok ? setConnected(true) : setConnected(false))
      .catch(() => setConnected(false));
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const send = async () => {
    if (!input.trim() || sending) return;
    const userMsg: ChatMessage = {
      id: Math.random().toString(36).slice(2),
      role: "user",
      content: input.trim(),
      timestamp: Date.now(),
    };
    const pendingMsg: ChatMessage = {
      id: "pending",
      role: "assistant",
      content: "",
      timestamp: Date.now(),
      pending: true,
    };
    setMessages((prev) => [...prev, userMsg, pendingMsg]);
    setInput("");
    setSending(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/api/v1/execution`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_id: "default",
          input: userMsg.content,
          timeout_seconds: 30,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }

      const data = await res.json();
      const reply = data.content || data.response || "(sin respuesta)";

      setMessages((prev) =>
        prev.map((m) =>
          m.id === "pending"
            ? { ...m, id: Math.random().toString(36).slice(2), content: reply, pending: false }
            : m
        )
      );
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : "Error desconocido";
      setMessages((prev) =>
        prev.map((m) =>
          m.id === "pending"
            ? { ...m, id: Math.random().toString(36).slice(2), content: `❌ ${errMsg}`, pending: false, error: true }
            : m
        )
      );
      setError(errMsg);
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const clearChat = () => {
    setMessages([]);
    setError(null);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 8rem)" }}>
      {/* Chat header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span style={{
            width: "0.5rem", height: "0.5rem", borderRadius: "9999px",
            background: connected ? "var(--success)" : "var(--danger)",
          }} />
          <span style={{ fontSize: "0.8rem", color: "var(--muted-fg)" }}>
            {connected ? "Backend conectado" : "Backend no disponible"}
          </span>
        </div>
        <button onClick={clearChat} style={smallBtn}>🗑️ Limpiar</button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} style={messagesContainerStyle}>
        {messages.length === 0 && (
          <div style={{ textAlign: "center", color: "var(--muted-fg)", padding: "2rem", fontSize: "0.875rem" }}>
            💬 Escribe un mensaje para conversar con el agente.
          </div>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            style={{
              display: "flex",
              justifyContent: msg.role === "user" ? "flex-end" : "flex-start",
              marginBottom: "0.5rem",
            }}
          >
            <div style={{
              ...bubbleStyle(msg.role, msg.pending, msg.error),
              maxWidth: "80%",
            }}>
              {msg.pending ? (
                <span style={{ color: "var(--muted-fg)" }}>● ● ●</span>
              ) : (
                msg.content
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Error */}
      {error && (
        <div style={errorBannerStyle}>⚠️ {error}</div>
      )}

      {/* Input */}
      <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Escribe un mensaje... (Enter para enviar)"
          rows={2}
          style={textareaStyle}
          disabled={sending}
        />
        <button onClick={send} disabled={sending || !input.trim()} style={sendBtn}>
          {sending ? "⏳" : "➤"}
        </button>
      </div>
    </div>
  );
}

// =====================================================================
// CONFIG VIEW (Proveedor IA)
// =====================================================================

function ConfigView() {
  const [config, setConfig] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [apiKey, setApiKey] = useState("");

  const [provider, setProvider] = useState("mock");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [temperature, setTemperature] = useState(0.5);
  const [topP, setTopP] = useState(1.0);
  const [maxTokens, setMaxTokens] = useState(1024);
  const [timeout, setTimeoutVal] = useState(60);

  const load = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/provider/config`);
      if (res.ok) {
        const cfg = await res.json();
        setConfig(cfg);
        setProvider(cfg.provider || "mock");
        setBaseUrl(cfg.base_url || "");
        setModel(cfg.model || "");
        setTemperature(cfg.temperature ?? 0.5);
        setTopP(cfg.top_p ?? 1.0);
        setMaxTokens(cfg.max_tokens ?? 1024);
        setTimeoutVal(cfg.timeout ?? 60);
      }
    } catch (e) {
      setMsg({ type: "err", text: "No se pudo cargar la configuración" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/provider/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, base_url: baseUrl, model, temperature, top_p: topP, max_tokens: maxTokens, timeout, stream: false }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMsg({ type: "ok", text: "Configuración guardada" });

      if (apiKey.trim()) {
        const keyRes = await fetch(`${API_BASE}/api/v1/provider/api-key`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ api_key: apiKey.trim() }),
        });
        if (!keyRes.ok) throw new Error("Error al guardar API key");
        setApiKey("");
        setMsg({ type: "ok", text: "Configuración + API key guardadas" });
      }
      await load();
    } catch (e) {
      setMsg({ type: "err", text: e instanceof Error ? e.message : "Error al guardar" });
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/provider/test`, { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        setMsg({ type: "ok", text: `✅ Conexión exitosa (${data.latency_ms?.toFixed(0) || "?"}ms)` });
      } else {
        setMsg({ type: "err", text: `❌ ${data.message || data.detail}` });
      }
    } catch (e) {
      setMsg({ type: "err", text: "No se pudo probar la conexión" });
    } finally {
      setTesting(false);
    }
  };

  const reset = async () => {
    if (!confirm("¿Restaurar configuración por defecto (Mock)?")) return;
    setSaving(true);
    try {
      await fetch(`${API_BASE}/api/v1/provider/reset`, { method: "POST" });
      await load();
      setMsg({ type: "ok", text: "Configuración restaurada" });
    } catch (e) {
      setMsg({ type: "err", text: "Error al restaurar" });
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div style={{ padding: "2rem", textAlign: "center", color: "var(--muted-fg)" }}>Cargando...</div>;

  return (
    <div>
      <h2 style={h2Style}>⚙️ Proveedor de IA</h2>

      {/* Status badges */}
      {config && (
        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", flexWrap: "wrap" }}>
          <Badge ok={config.provider !== "mock"} text={config.provider === "mock" ? "Mock" : config.provider} />
          <Badge ok={config.api_key_set} text={config.api_key_set ? "Key configurada" : "Sin key"} />
          <Badge ok={config.last_test_ok === true} text={config.last_test_ok === true ? "Probado OK" : config.last_test_ok === false ? "Probado FAIL" : "Sin probar"} />
        </div>
      )}

      {msg && (
        <div style={{ ...msgStyle, background: msg.type === "ok" ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)", color: msg.type === "ok" ? "var(--success)" : "var(--danger)" }}>
          {msg.text}
        </div>
      )}

      <div style={cardStyle}>
        <div style={fieldStyle}>
          <label style={labelStyle}>Proveedor</label>
          <select value={provider} onChange={(e) => { setProvider(e.target.value); if (e.target.value === "nvidia") { setBaseUrl("https://integrate.api.nvidia.com/v1"); setModel("z-ai/glm-5.3"); } }} style={inputStyle}>
            <option value="mock">Mock (offline)</option>
            <option value="nvidia">NVIDIA NIM</option>
          </select>
        </div>

        <div style={fieldStyle}>
          <label style={labelStyle}>Base URL</label>
          <input type="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://integrate.api.nvidia.com/v1" style={inputStyle} disabled={provider === "mock"} />
        </div>

        <div style={fieldStyle}>
          <label style={labelStyle}>Modelo</label>
          <input type="text" value={model} onChange={(e) => setModel(e.target.value)} placeholder="z-ai/glm-5.3" style={inputStyle} disabled={provider === "mock"} />
        </div>

        <div style={fieldStyle}>
          <label style={labelStyle}>API Key {config?.api_key_set && "(ya configurada — dejar vacío para mantener)"}</label>
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={config?.api_key_set ? "••••••••" : "nvapi-..."} style={inputStyle} disabled={provider === "mock"} autoComplete="off" />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
          <div style={fieldStyle}>
            <label style={labelStyle}>Temperatura: {temperature.toFixed(2)}</label>
            <input type="range" min="0" max="2" step="0.05" value={temperature} onChange={(e) => setTemperature(parseFloat(e.target.value))} style={{ width: "100%" }} />
          </div>
          <div style={fieldStyle}>
            <label style={labelStyle}>top_p: {topP.toFixed(2)}</label>
            <input type="range" min="0" max="1" step="0.05" value={topP} onChange={(e) => setTopP(parseFloat(e.target.value))} style={{ width: "100%" }} />
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "1rem" }}>
          <div style={fieldStyle}>
            <label style={labelStyle}>Max tokens</label>
            <input type="number" min="1" max="32768" value={maxTokens} onChange={(e) => setMaxTokens(parseInt(e.target.value) || 1024)} style={inputStyle} />
          </div>
          <div style={fieldStyle}>
            <label style={labelStyle}>Timeout (s)</label>
            <input type="number" min="1" max="600" value={timeout} onChange={(e) => setTimeoutVal(parseInt(e.target.value) || 60)} style={inputStyle} />
          </div>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "1rem" }}>
          <button onClick={save} disabled={saving} style={primaryBtn}>{saving ? "⏳" : "💾 Guardar"}</button>
          <button onClick={test} disabled={testing} style={secondaryBtn}>{testing ? "⏳" : "🔌 Probar"}</button>
          <button onClick={reset} disabled={saving} style={secondaryBtn}>↩️ Reset</button>
        </div>
      </div>
    </div>
  );
}

// =====================================================================
// TELEGRAM VIEW
// =====================================================================

function TelegramView() {
  const [token, setToken] = useState("");
  const [tokenSet, setTokenSet] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [status, setStatus] = useState<"unknown" | "connected" | "disconnected">("unknown");
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const load = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/telegram/config`);
      if (res.ok) {
        const data = await res.json();
        setTokenSet(data.token_set || false);
        setEnabled(data.enabled || false);
        setStatus(data.connected ? "connected" : "disconnected");
      }
    } catch (e) {
      // Endpoint may not exist yet — show "not configured"
      setStatus("unknown");
    }
  };

  useEffect(() => { load(); }, []);

  const saveToken = async () => {
    if (!token.trim()) return;
    setSaving(true);
    setMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/telegram/token`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: token.trim() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setToken("");
      setTokenSet(true);
      setMsg({ type: "ok", text: "Token guardado (cifrado en servidor)" });
      await load();
    } catch (e) {
      setMsg({ type: "err", text: "No se pudo guardar el token. ¿Está el endpoint disponible?" });
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    setTesting(true);
    setMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/telegram/test`, { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        setStatus("connected");
        setMsg({ type: "ok", text: `✅ Bot conectado: @${data.bot_username || "?"}` });
      } else {
        setStatus("disconnected");
        setMsg({ type: "err", text: `❌ ${data.message || "No se pudo conectar"}` });
      }
    } catch (e) {
      setMsg({ type: "err", text: "Endpoint no disponible. Configura el backend primero." });
    } finally {
      setTesting(false);
    }
  };

  const toggleEnabled = async () => {
    const newVal = !enabled;
    setEnabled(newVal);
    setMsg(null);
    try {
      await fetch(`${API_BASE}/api/v1/telegram/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: newVal }),
      });
      setMsg({ type: "ok", text: newVal ? "Telegram activado" : "Telegram desactivado" });
    } catch (e) {
      setMsg({ type: "err", text: "No se pudo cambiar el estado" });
      setEnabled(!newVal); // revert
    }
  };

  return (
    <div>
      <h2 style={h2Style}>✈️ Telegram</h2>

      {msg && (
        <div style={{ ...msgStyle, background: msg.type === "ok" ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)", color: msg.type === "ok" ? "var(--success)" : "var(--danger)" }}>
          {msg.text}
        </div>
      )}

      {/* Status */}
      <div style={{ ...cardStyle, marginBottom: "1rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span style={{
              width: "0.75rem", height: "0.75rem", borderRadius: "9999px",
              background: status === "connected" ? "var(--success)" : status === "disconnected" ? "var(--danger)" : "var(--muted-fg)",
            }} />
            <span style={{ fontSize: "0.875rem", fontWeight: 500 }}>
              {status === "connected" ? "Conectado" : status === "disconnected" ? "Desconectado" : "Sin configurar"}
            </span>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", cursor: "pointer" }}>
            <input type="checkbox" checked={enabled} onChange={toggleEnabled} disabled={!tokenSet} />
            <span style={{ fontSize: "0.8rem" }}>{enabled ? "Activo" : "Inactivo"}</span>
          </label>
        </div>
      </div>

      {/* Token */}
      <div style={cardStyle}>
        <div style={fieldStyle}>
          <label style={labelStyle}>
            Bot Token {tokenSet && "(token ya configurado — dejar vacío para mantener)"}
          </label>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={tokenSet ? "•••••••• (configurado)" : "123456789:ABCdef..."}
              style={{ ...inputStyle, flex: 1 }}
              autoComplete="off"
            />
            <button onClick={saveToken} disabled={saving || !token.trim()} style={primaryBtn}>
              {saving ? "⏳" : "💾"}
            </button>
          </div>
          <p style={hintStyle}>
            Obtén tu token hablando con <a href="https://t.me/BotFather" target="_blank" rel="noopener" style={{ color: "var(--info)" }}>@BotFather</a> en Telegram.
          </p>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
          <button onClick={testConnection} disabled={testing || !tokenSet} style={secondaryBtn}>
            {testing ? "⏳ Verificando..." : "🔌 Verificar conexión"}
          </button>
        </div>

        {!tokenSet && (
          <div style={{ ...msgStyle, background: "rgba(245,158,11,0.1)", color: "var(--warning)", marginTop: "1rem" }}>
            ⚠️ Configura el token del bot para activar Telegram.
          </div>
        )}
      </div>
    </div>
  );
}

// =====================================================================
// WHATSAPP VIEW
// =====================================================================

function WhatsAppView() {
  const [method, setMethod] = useState<"cloud" | "qr">("cloud");
  const [phoneNumberId, setPhoneNumberId] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [verifyToken, setVerifyToken] = useState("");
  const [credentialsSet, setCredentialsSet] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [status, setStatus] = useState<"unknown" | "connected" | "disconnected">("unknown");
  const [qrCode, setQrCode] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  const load = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/whatsapp/config`);
      if (res.ok) {
        const data = await res.json();
        setCredentialsSet(data.credentials_set || false);
        setEnabled(data.enabled || false);
        setStatus(data.connected ? "connected" : "disconnected");
        setMethod(data.method || "cloud");
        setPhoneNumberId(data.phone_number_id || "");
        setVerifyToken(data.verify_token || "");
      }
    } catch (e) {
      setStatus("unknown");
    }
  };

  useEffect(() => { load(); }, []);

  const saveCredentials = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const body: any = { method };
      if (method === "cloud") {
        if (!phoneNumberId.trim() || !accessToken.trim()) {
          setMsg({ type: "err", text: "Phone Number ID y Access Token son obligatorios" });
          setSaving(false);
          return;
        }
        body.phone_number_id = phoneNumberId.trim();
        body.access_token = accessToken.trim();
        body.verify_token = verifyToken.trim() || Math.random().toString(36).slice(2);
      }
      const res = await fetch(`${API_BASE}/api/v1/whatsapp/credentials`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setAccessToken("");
      setCredentialsSet(true);
      setMsg({ type: "ok", text: "Credenciales guardadas (cifradas en servidor)" });
      await load();
    } catch (e) {
      setMsg({ type: "err", text: "No se pudieron guardar las credenciales. ¿Está el endpoint disponible?" });
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    setTesting(true);
    setMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/whatsapp/test`, { method: "POST" });
      const data = await res.json();
      if (data.ok) {
        setStatus("connected");
        setMsg({ type: "ok", text: "✅ WhatsApp conectado" });
      } else {
        setStatus("disconnected");
        setMsg({ type: "err", text: `❌ ${data.message || "No conectado"}` });
      }
    } catch (e) {
      setMsg({ type: "err", text: "Endpoint no disponible. Configura el backend primero." });
    } finally {
      setTesting(false);
    }
  };

  const toggleEnabled = async () => {
    const newVal = !enabled;
    setEnabled(newVal);
    try {
      await fetch(`${API_BASE}/api/v1/whatsapp/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: newVal }),
      });
      setMsg({ type: "ok", text: newVal ? "WhatsApp activado" : "WhatsApp desactivado" });
    } catch (e) {
      setEnabled(!newVal);
      setMsg({ type: "err", text: "No se pudo cambiar el estado" });
    }
  };

  return (
    <div>
      <h2 style={h2Style}>📱 WhatsApp</h2>

      {msg && (
        <div style={{ ...msgStyle, background: msg.type === "ok" ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)", color: msg.type === "ok" ? "var(--success)" : "var(--danger)" }}>
          {msg.text}
        </div>
      )}

      {/* Status */}
      <div style={{ ...cardStyle, marginBottom: "1rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span style={{
              width: "0.75rem", height: "0.75rem", borderRadius: "9999px",
              background: status === "connected" ? "var(--success)" : status === "disconnected" ? "var(--danger)" : "var(--muted-fg)",
            }} />
            <span style={{ fontSize: "0.875rem", fontWeight: 500 }}>
              {status === "connected" ? "Conectado" : status === "disconnected" ? "Desconectado" : "Sin configurar"}
            </span>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", cursor: "pointer" }}>
            <input type="checkbox" checked={enabled} onChange={toggleEnabled} disabled={!credentialsSet} />
            <span style={{ fontSize: "0.8rem" }}>{enabled ? "Activo" : "Inactivo"}</span>
          </label>
        </div>
      </div>

      {/* Method selector */}
      <div style={cardStyle}>
        <div style={fieldStyle}>
          <label style={labelStyle}>Método de conexión</label>
          <select value={method} onChange={(e) => setMethod(e.target.value as "cloud" | "qr")} style={inputStyle}>
            <option value="cloud">WhatsApp Business Cloud API (recomendado)</option>
            <option value="qr">WhatsApp Web QR (no oficial)</option>
          </select>
        </div>

        {method === "cloud" ? (
          <>
            <div style={fieldStyle}>
              <label style={labelStyle}>Phone Number ID</label>
              <input type="text" value={phoneNumberId} onChange={(e) => setPhoneNumberId(e.target.value)} placeholder="123456789012345" style={inputStyle} />
            </div>
            <div style={fieldStyle}>
              <label style={labelStyle}>Access Token {credentialsSet && "(configurado — dejar vacío para mantener)"}</label>
              <input type="password" value={accessToken} onChange={(e) => setAccessToken(e.target.value)} placeholder="EAAG..." style={inputStyle} autoComplete="off" />
            </div>
            <div style={fieldStyle}>
              <label style={labelStyle}>Webhook Verify Token</label>
              <input type="text" value={verifyToken} onChange={(e) => setVerifyToken(e.target.value)} placeholder="mi-verify-token" style={inputStyle} />
              <p style={hintStyle}>
                Configura el webhook en Meta Business: <code style={{ fontSize: "0.7rem" }}>{API_BASE}/api/v1/whatsapp/webhook</code>
              </p>
            </div>
          </>
        ) : (
          <div style={{ padding: "1rem", textAlign: "center", color: "var(--muted-fg)" }}>
            <p style={{ fontSize: "0.8rem" }}>
              El método QR requiere un daemon de WhatsApp Web. Escanea el código QR desde tu teléfono.
            </p>
            {qrCode ? (
              <img src={qrCode} alt="QR Code" style={{ maxWidth: "200px", margin: "1rem auto" }} />
            ) : (
              <div style={{ padding: "2rem", color: "var(--muted-fg)", fontSize: "0.8rem" }}>
                QR no disponible — el daemon no está corriendo.
              </div>
            )}
          </div>
        )}

        <div style={{ display: "flex", gap: "0.5rem", marginTop: "1rem" }}>
          <button onClick={saveCredentials} disabled={saving} style={primaryBtn}>
            {saving ? "⏳" : "💾 Guardar credenciales"}
          </button>
          <button onClick={testConnection} disabled={testing || !credentialsSet} style={secondaryBtn}>
            {testing ? "⏳" : "🔌 Verificar"}
          </button>
        </div>

        {!credentialsSet && (
          <div style={{ ...msgStyle, background: "rgba(245,158,11,0.1)", color: "var(--warning)", marginTop: "1rem" }}>
            ⚠️ Configura las credenciales para activar WhatsApp.
          </div>
        )}
      </div>
    </div>
  );
}

// =====================================================================
// Shared components + styles
// =====================================================================

function Badge({ ok, text }: { ok: boolean; text: string }) {
  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: "0.5rem",
      padding: "0.4rem 0.6rem",
      background: "var(--card)",
      fontSize: "0.7rem",
      display: "flex",
      alignItems: "center",
      gap: "0.3rem",
    }}>
      <span style={{
        width: "0.4rem", height: "0.4rem", borderRadius: "9999px",
        background: ok ? "var(--success)" : "var(--muted-fg)",
      }} />
      {text}
    </div>
  );
}

// Need useRef import
import { useRef } from "react";

// Styles
const headerStyle: React.CSSProperties = {
  borderBottom: "1px solid var(--border)",
  padding: "0.5rem 1rem",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "1rem",
  position: "sticky",
  top: 0,
  background: "var(--bg)",
  zIndex: 10,
};

const navBtn: React.CSSProperties = {
  border: "none",
  background: "transparent",
  cursor: "pointer",
  padding: "0.4rem 0.6rem",
  borderRadius: "0.5rem",
  display: "flex",
  alignItems: "center",
  transition: "background 0.15s",
};

const themeBtn: React.CSSProperties = {
  background: "var(--muted)",
  border: "1px solid var(--border)",
  borderRadius: "0.5rem",
  padding: "0.3rem 0.6rem",
  cursor: "pointer",
  fontSize: "1rem",
};

const cardStyle: React.CSSProperties = {
  border: "1px solid var(--border)",
  borderRadius: "0.75rem",
  padding: "1.5rem",
  background: "var(--card)",
};

const h2Style: React.CSSProperties = {
  fontSize: "1.25rem",
  fontWeight: 600,
  margin: "0 0 1rem",
};

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

const fieldStyle: React.CSSProperties = {
  marginBottom: "1rem",
};

const hintStyle: React.CSSProperties = {
  fontSize: "0.7rem",
  color: "var(--muted-fg)",
  margin: "0.25rem 0 0",
};

const primaryBtn: React.CSSProperties = {
  background: "var(--accent)",
  color: "var(--accent-fg)",
  border: "1px solid var(--accent)",
  borderRadius: "0.5rem",
  padding: "0.5rem 1rem",
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
  background: "transparent",
  border: "1px solid var(--border)",
  borderRadius: "0.375rem",
  padding: "0.25rem 0.6rem",
  fontSize: "0.75rem",
  cursor: "pointer",
  color: "var(--muted-fg)",
};

const msgStyle: React.CSSProperties = {
  padding: "0.6rem 0.8rem",
  borderRadius: "0.5rem",
  fontSize: "0.8rem",
  marginBottom: "1rem",
};

const errorBannerStyle: React.CSSProperties = {
  background: "rgba(239,68,68,0.1)",
  color: "var(--danger)",
  padding: "0.5rem 0.75rem",
  borderRadius: "0.375rem",
  fontSize: "0.75rem",
  marginBottom: "0.5rem",
};

const messagesContainerStyle: React.CSSProperties = {
  flex: 1,
  overflowY: "auto",
  border: "1px solid var(--border)",
  borderRadius: "0.75rem",
  background: "var(--card)",
  padding: "1rem",
  display: "flex",
  flexDirection: "column",
  gap: "0.5rem",
};

function bubbleStyle(role: string, pending?: boolean, error?: boolean): React.CSSProperties {
  const isUser = role === "user";
  return {
    background: error
      ? "rgba(239,68,68,0.1)"
      : isUser
        ? "var(--chat-user)"
        : "var(--chat-agent)",
    color: error
      ? "var(--danger)"
      : isUser
        ? "#ffffff"
        : "var(--fg)",
    padding: "0.6rem 0.875rem",
    borderRadius: "0.75rem",
    fontSize: "0.875rem",
    lineHeight: 1.5,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  };
}

const textareaStyle: React.CSSProperties = {
  flex: 1,
  background: "var(--card)",
  color: "var(--fg)",
  border: "1px solid var(--border)",
  borderRadius: "0.5rem",
  padding: "0.5rem 0.75rem",
  fontSize: "0.875rem",
  resize: "none",
};

const sendBtn: React.CSSProperties = {
  background: "var(--accent)",
  color: "var(--accent-fg)",
  border: "1px solid var(--accent)",
  borderRadius: "0.5rem",
  padding: "0 1rem",
  fontSize: "1.2rem",
  cursor: "pointer",
};
