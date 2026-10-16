"use client";

import { useState, useEffect, useRef } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "https://agente-general.onrender.com";

type Tab = "chat" | "settings";
type SettingsSection = "provider" | "telegram" | "whatsapp";

export default function Home() {
  const [tab, setTab] = useState<Tab>("chat");
  const [section, setSection] = useState<SettingsSection>("provider");
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

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <header style={headerStyle}>
        <strong style={{ fontSize: "0.9rem" }}>🤖 AGENTE GENERAL</strong>
        <nav style={{ display: "flex", gap: "0.25rem" }}>
          <NavBtn active={tab === "chat"} onClick={() => setTab("chat")}>💬 Chat</NavBtn>
          <NavBtn active={tab === "settings"} onClick={() => setTab("settings")}>⚙️ Configuración</NavBtn>
        </nav>
        <button onClick={toggleTheme} style={themeBtn}>{dark ? "☀️" : "🌙"}</button>
      </header>

      <main style={{ flex: 1, maxWidth: "56rem", margin: "0 auto", width: "100%", padding: "1rem" }}>
        {tab === "chat" && <ChatView />}
        {tab === "settings" && (
          <SettingsView section={section} setSection={setSection} />
        )}
      </main>
    </div>
  );
}

function NavBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        ...navBtn,
        background: active ? "var(--accent)" : "transparent",
        color: active ? "var(--accent-fg)" : "var(--muted-fg)",
        borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
      }}
    >
      {children}
    </button>
  );
}

// =====================================================================
// SETTINGS VIEW — tabs for Provider, Telegram, WhatsApp
// =====================================================================

function SettingsView({ section, setSection }: { section: SettingsSection; setSection: (s: SettingsSection) => void }) {
  const tabs: { key: SettingsSection; label: string; icon: string }[] = [
    { key: "provider", label: "Proveedor IA", icon: "🧠" },
    { key: "telegram", label: "Telegram", icon: "✈️" },
    { key: "whatsapp", label: "WhatsApp", icon: "📱" },
  ];

  return (
    <div>
      {/* Sub-tabs */}
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", borderBottom: "1px solid var(--border)", paddingBottom: "0.5rem" }}>
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setSection(t.key)}
            style={{
              ...subTabBtn,
              background: section === t.key ? "var(--muted)" : "transparent",
              color: section === t.key ? "var(--fg)" : "var(--muted-fg)",
              fontWeight: section === t.key ? 600 : 400,
              borderBottom: section === t.key ? "2px solid var(--accent)" : "2px solid transparent",
            }}
          >
            <span style={{ marginRight: "0.3rem" }}>{t.icon}</span>
            {t.label}
          </button>
        ))}
      </div>

      {section === "provider" && <ProviderView />}
      {section === "telegram" && <TelegramView />}
      {section === "whatsapp" && <WhatsAppView />}
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
    fetch(`${API_BASE}/api/v1/health`)
      .then((r) => setConnected(r.ok))
      .catch(() => setConnected(false));
  }, []);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  const send = async () => {
    if (!input.trim() || sending) return;
    const userMsg: ChatMessage = { id: Math.random().toString(36).slice(2), role: "user", content: input.trim() };
    const pendingMsg: ChatMessage = { id: "pending", role: "assistant", content: "", pending: true };
    setMessages((p) => [...p, userMsg, pendingMsg]);
    setInput("");
    setSending(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/api/v1/execution`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_id: "default", input: userMsg.content, timeout_seconds: 30 }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const reply = data.content || data.response || "(sin respuesta)";
      setMessages((p) => p.map((m) => m.id === "pending" ? { ...m, id: Math.random().toString(36).slice(2), content: reply, pending: false } : m));
    } catch (e) {
      const err = e instanceof Error ? e.message : "Error";
      setMessages((p) => p.map((m) => m.id === "pending" ? { ...m, id: Math.random().toString(36).slice(2), content: `❌ ${err}`, pending: false, error: true } : m));
      setError(err);
    } finally {
      setSending(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 8rem)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span style={{ width: "0.5rem", height: "0.5rem", borderRadius: "9999px", background: connected ? "var(--success)" : "var(--danger)" }} />
          <span style={{ fontSize: "0.8rem", color: "var(--muted-fg)" }}>{connected ? "Backend conectado" : "Backend no disponible"}</span>
        </div>
        <button onClick={() => { setMessages([]); setError(null); }} style={smallBtn}>🗑️ Limpiar</button>
      </div>

      <div ref={scrollRef} style={messagesContainerStyle}>
        {messages.length === 0 && (
          <div style={{ textAlign: "center", color: "var(--muted-fg)", padding: "2rem", fontSize: "0.875rem" }}>
            💬 Escribe un mensaje para conversar con el agente.
          </div>
        )}
        {messages.map((msg) => (
          <div key={msg.id} style={{ display: "flex", justifyContent: msg.role === "user" ? "flex-end" : "flex-start", marginBottom: "0.5rem" }}>
            <div style={{ ...bubbleStyle(msg.role, msg.pending, msg.error), maxWidth: "80%" }}>
              {msg.pending ? <span style={{ color: "var(--muted-fg)" }}>● ● ●</span> : msg.content}
            </div>
          </div>
        ))}
      </div>

      {error && <div style={errorBannerStyle}>⚠️ {error}</div>}

      <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder="Escribe un mensaje... (Enter para enviar)"
          rows={2}
          style={textareaStyle}
          disabled={sending}
        />
        <button onClick={send} disabled={sending || !input.trim()} style={sendBtn}>{sending ? "⏳" : "➤"}</button>
      </div>
    </div>
  );
}

// =====================================================================
// PROVIDER VIEW (IA)
// =====================================================================

function ProviderView() {
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
  const [timeoutVal, setTimeoutVal] = useState(60);

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
    } catch {}
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/provider/config`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, base_url: baseUrl, model, temperature, top_p: topP, max_tokens: maxTokens, timeout: timeoutVal, stream: false }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMsg({ type: "ok", text: "Configuración guardada" });
      if (apiKey.trim()) {
        const kr = await fetch(`${API_BASE}/api/v1/provider/api-key`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: apiKey.trim() }) });
        if (!kr.ok) throw new Error("Error API key");
        setApiKey("");
        setMsg({ type: "ok", text: "Configuración + API key guardadas" });
      }
      await load();
    } catch (e) { setMsg({ type: "err", text: e instanceof Error ? e.message : "Error" }); }
    finally { setSaving(false); }
  };

  const test = async () => {
    setTesting(true); setMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/provider/test`, { method: "POST" });
      const d = await res.json();
      setMsg(d.ok ? { type: "ok", text: `✅ ${d.latency_ms?.toFixed(0) || "?"}ms` } : { type: "err", text: `❌ ${d.message || d.detail}` });
    } catch { setMsg({ type: "err", text: "No se pudo probar" }); }
    finally { setTesting(false); }
  };

  const reset = async () => {
    if (!confirm("¿Restaurar defaults (Mock)?")) return;
    setSaving(true);
    try { await fetch(`${API_BASE}/api/v1/provider/reset`, { method: "POST" }); await load(); setMsg({ type: "ok", text: "Restaurado" }); }
    catch { setMsg({ type: "err", text: "Error" }); }
    finally { setSaving(false); }
  };

  if (loading) return <div style={{ padding: "2rem", textAlign: "center", color: "var(--muted-fg)" }}>Cargando...</div>;

  return (
    <div>
      <h2 style={h2Style}>🧠 Proveedor de IA</h2>
      {msg && <div style={{ ...msgStyle, color: msg.type === "ok" ? "var(--success)" : "var(--danger)", background: msg.type === "ok" ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)" }}>{msg.text}</div>}

      {config && (
        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", flexWrap: "wrap" }}>
          <Badge ok={config.provider !== "mock"} text={config.provider} />
          <Badge ok={config.api_key_set} text={config.api_key_set ? "Key ✓" : "Sin key"} />
          <Badge ok={config.last_test_ok === true} text={config.last_test_ok === true ? "Test OK" : "Sin test"} />
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
          <label style={labelStyle}>API Key {config?.api_key_set && "(configurada)"}</label>
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
            <input type="number" min="1" max="600" value={timeoutVal} onChange={(e) => setTimeoutVal(parseInt(e.target.value) || 60)} style={inputStyle} />
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

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/telegram/config`).then(r => r.ok ? r.json() : null).then(d => {
      if (d) { setTokenSet(d.token_set || false); setEnabled(d.enabled || false); setStatus(d.connected ? "connected" : "disconnected"); }
    }).catch(() => setStatus("unknown"));
  }, []);

  const saveToken = async () => {
    if (!token.trim()) return;
    setSaving(true); setMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/telegram/token`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: token.trim() }) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setToken(""); setTokenSet(true);
      setMsg({ type: "ok", text: "Token guardado (cifrado)" });
    } catch { setMsg({ type: "err", text: "No se pudo guardar. ¿Endpoint disponible?" }); }
    finally { setSaving(false); }
  };

  const testConn = async () => {
    setTesting(true); setMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/telegram/test`, { method: "POST" });
      const d = await res.json();
      if (d.ok) { setStatus("connected"); setMsg({ type: "ok", text: `✅ @${d.bot_username || "?"}` }); }
      else { setStatus("disconnected"); setMsg({ type: "err", text: `❌ ${d.message || ""}` }); }
    } catch { setMsg({ type: "err", text: "Endpoint no disponible" }); }
    finally { setTesting(false); }
  };

  const toggle = async () => {
    const v = !enabled; setEnabled(v);
    try { await fetch(`${API_BASE}/api/v1/telegram/toggle`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: v }) }); setMsg({ type: "ok", text: v ? "Activado" : "Desactivado" }); }
    catch { setEnabled(!v); setMsg({ type: "err", text: "Error" }); }
  };

  return (
    <div>
      <h2 style={h2Style}>✈️ Telegram</h2>
      {msg && <div style={{ ...msgStyle, color: msg.type === "ok" ? "var(--success)" : "var(--danger)", background: msg.type === "ok" ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)" }}>{msg.text}</div>}

      <div style={{ ...cardStyle, marginBottom: "1rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span style={{ width: "0.75rem", height: "0.75rem", borderRadius: "9999px", background: status === "connected" ? "var(--success)" : status === "disconnected" ? "var(--danger)" : "var(--muted-fg)" }} />
            <span style={{ fontSize: "0.875rem", fontWeight: 500 }}>{status === "connected" ? "Conectado" : status === "disconnected" ? "Desconectado" : "Sin configurar"}</span>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", cursor: "pointer" }}>
            <input type="checkbox" checked={enabled} onChange={toggle} disabled={!tokenSet} />
            <span style={{ fontSize: "0.8rem" }}>{enabled ? "Activo" : "Inactivo"}</span>
          </label>
        </div>
      </div>

      <div style={cardStyle}>
        <div style={fieldStyle}>
          <label style={labelStyle}>Bot Token {tokenSet && "(configurado)"}</label>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <input type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder={tokenSet ? "••••••••" : "123456789:ABC..."} style={{ ...inputStyle, flex: 1 }} autoComplete="off" />
            <button onClick={saveToken} disabled={saving || !token.trim()} style={primaryBtn}>{saving ? "⏳" : "💾"}</button>
          </div>
          <p style={hintStyle}>Obtén tu token en <a href="https://t.me/BotFather" target="_blank" rel="noopener" style={{ color: "var(--info)" }}>@BotFather</a></p>
        </div>
        <button onClick={testConn} disabled={testing || !tokenSet} style={secondaryBtn}>{testing ? "⏳ Verificando..." : "🔌 Verificar conexión"}</button>
        {!tokenSet && <div style={{ ...msgStyle, color: "var(--warning)", background: "rgba(245,158,11,0.1)", marginTop: "1rem" }}>⚠️ Configura el token del bot.</div>}
      </div>
    </div>
  );
}

// =====================================================================
// WHATSAPP VIEW — QR flow like CRM-ALBRA
// =====================================================================

function WhatsAppView() {
  const [status, setStatus] = useState<"unknown" | "connected" | "disconnected" | "linking">("unknown");
  const [qrCode, setQrCode] = useState<string | null>(null);
  const [phone, setPhone] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStatus = async () => {
    try {
      const res = await fetch(`/api/whatsapp/daemon-proxy?path=/status`);
      if (!res.ok) { setStatus("unknown"); return; }
      const data = await res.json();
      if (data.status === "connected") {
        setStatus("connected");
        setPhone(data.phone || null);
        setQrCode(null);
      } else if (data.status === "qr_ready" || data.qr) {
        setStatus("linking");
        setQrCode(data.qr || null);
      } else {
        setStatus("disconnected");
        setQrCode(null);
      }
    } catch {
      setStatus("unknown");
    }
  };

  useEffect(() => {
    loadStatus().finally(() => setLoading(false));
    // Poll status every 3s when linking (waiting for QR scan)
    pollRef.current = setInterval(() => {
      if (status === "linking") loadStatus();
    }, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [status]);

  const connect = async () => {
    setConnecting(true);
    setMsg(null);
    try {
      const res = await fetch(`/api/whatsapp/daemon-proxy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: "/connect" }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMsg({ type: "ok", text: "Iniciando conexión... Escanea el QR" });
      setStatus("linking");
      // Start polling for QR
      setTimeout(loadStatus, 1000);
    } catch (e) {
      setMsg({ type: "err", text: "No se pudo conectar. ¿Está el daemon corriendo?" });
      setStatus("disconnected");
    } finally {
      setConnecting(false);
    }
  };

  const disconnect = async () => {
    if (!confirm("¿Desconectar WhatsApp?")) return;
    try {
      await fetch(`/api/whatsapp/daemon-proxy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: "/disconnect" }),
      });
      setStatus("disconnected");
      setPhone(null);
      setQrCode(null);
      setMsg({ type: "ok", text: "Desconectado" });
    } catch {
      setMsg({ type: "err", text: "Error al desconectar" });
    }
  };

  const refreshQr = async () => {
    try {
      const res = await fetch(`/api/whatsapp/daemon-proxy?path=/qr`);
      if (res.ok) {
        const data = await res.json();
        if (data.qr) setQrCode(data.qr);
      }
    } catch {}
  };

  if (loading) return <div style={{ padding: "2rem", textAlign: "center", color: "var(--muted-fg)" }}>Cargando WhatsApp...</div>;

  return (
    <div>
      <h2 style={h2Style}>📱 WhatsApp</h2>
      {msg && <div style={{ ...msgStyle, color: msg.type === "ok" ? "var(--success)" : "var(--danger)", background: msg.type === "ok" ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)" }}>{msg.text}</div>}

      {/* Status card */}
      <div style={{ ...cardStyle, marginBottom: "1rem" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span style={{ width: "0.75rem", height: "0.75rem", borderRadius: "9999px", background: status === "connected" ? "var(--success)" : status === "linking" ? "var(--warning)" : status === "disconnected" ? "var(--danger)" : "var(--muted-fg)" }} />
            <span style={{ fontSize: "0.875rem", fontWeight: 500 }}>
              {status === "connected" ? `Conectado${phone ? ` — ${phone}` : ""}` : status === "linking" ? "Esperando QR..." : status === "disconnected" ? "Desconectado" : "Sin configurar"}
            </span>
          </div>
          {status === "connected" ? (
            <button onClick={disconnect} style={{ ...secondaryBtn, color: "var(--danger)" }}>🔌 Desconectar</button>
          ) : (
            <button onClick={connect} disabled={connecting || status === "linking"} style={primaryBtn}>
              {connecting ? "⏳" : status === "linking" ? "🔄 Esperando..." : "📱 Conectar por QR"}
            </button>
          )}
        </div>
      </div>

      {/* QR code area */}
      {status === "linking" && (
        <div style={cardStyle}>
          <div style={{ textAlign: "center", padding: "1rem" }}>
            <h3 style={{ fontSize: "1rem", fontWeight: 600, marginBottom: "0.5rem" }}>Escanea el código QR</h3>
            <p style={{ fontSize: "0.8rem", color: "var(--muted-fg)", marginBottom: "1rem" }}>
              Abre WhatsApp en tu teléfono → Configuración → Dispositivos vinculados → Escanear código QR
            </p>
            {qrCode ? (
              <div style={{ display: "flex", justifyContent: "center" }}>
                {qrCode.startsWith("data:image") || qrCode.startsWith("http") ? (
                  <img src={qrCode} alt="QR Code" style={{ maxWidth: "256px", borderRadius: "0.5rem" }} />
                ) : (
                  <div style={{
                    width: "256px", height: "256px",
                    background: "var(--muted)", borderRadius: "0.5rem",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: "0.8rem", color: "var(--muted-fg)", textAlign: "center", padding: "1rem",
                  }}>
                    {qrCode}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ width: "256px", height: "256px", margin: "0 auto", background: "var(--muted)", borderRadius: "0.5rem", display: "flex", alignItems: "center", justifyContent: "center" }}>
                <span style={{ fontSize: "0.8rem", color: "var(--muted-fg)" }}>Generando QR...</span>
              </div>
            )}
            <button onClick={refreshQr} style={{ ...secondaryBtn, marginTop: "1rem" }}>🔄 Refrescar QR</button>
          </div>
        </div>
      )}

      {/* Disconnected info */}
      {status === "disconnected" && (
        <div style={cardStyle}>
          <div style={{ textAlign: "center", padding: "2rem" }}>
            <div style={{ fontSize: "3rem", marginBottom: "1rem" }}>📱</div>
            <h3 style={{ fontSize: "1rem", fontWeight: 600, marginBottom: "0.5rem" }}>WhatsApp no conectado</h3>
            <p style={{ fontSize: "0.8rem", color: "var(--muted-fg)", maxWidth: "24rem", margin: "0 auto 1rem" }}>
              Conecta tu WhatsApp escaneando un código QR. El agente podrá responder
              automáticamente, hacer seguimiento y transferir a humanos.
            </p>
            <button onClick={connect} disabled={connecting} style={primaryBtn}>
              {connecting ? "⏳ Conectando..." : "📱 Conectar por QR"}
            </button>
          </div>
        </div>
      )}

      {/* Daemon not available */}
      {status === "unknown" && (
        <div style={cardStyle}>
          <div style={{ textAlign: "center", padding: "2rem" }}>
            <div style={{ fontSize: "3rem", marginBottom: "1rem" }}>⚠️</div>
            <h3 style={{ fontSize: "1rem", fontWeight: 600, marginBottom: "0.5rem" }}>Daemon no disponible</h3>
            <p style={{ fontSize: "0.8rem", color: "var(--muted-fg)", maxWidth: "24rem", margin: "0 auto" }}>
              El servicio de WhatsApp (daemon Baileys) no está corriendo.
              Debes iniciar el daemon en el backend para usar WhatsApp por QR.
            </p>
            <p style={{ fontSize: "0.7rem", color: "var(--muted-fg)", marginTop: "1rem" }}>
              Variable de entorno: <code>WHATSAPP_DAEMON_URL</code> (default: http://localhost:3002)
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// =====================================================================
// Shared components + styles
// =====================================================================

function Badge({ ok, text }: { ok: boolean; text: string }) {
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "0.5rem", padding: "0.4rem 0.6rem", background: "var(--card)", fontSize: "0.7rem", display: "flex", alignItems: "center", gap: "0.3rem" }}>
      <span style={{ width: "0.4rem", height: "0.4rem", borderRadius: "9999px", background: ok ? "var(--success)" : "var(--muted-fg)" }} />
      {text}
    </div>
  );
}

const headerStyle: React.CSSProperties = {
  borderBottom: "1px solid var(--border)", padding: "0.5rem 1rem",
  display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem",
  position: "sticky", top: 0, background: "var(--bg)", zIndex: 10,
};
const navBtn: React.CSSProperties = { border: "none", background: "transparent", cursor: "pointer", padding: "0.4rem 0.6rem", borderRadius: "0.5rem", display: "flex", alignItems: "center", fontSize: "0.8rem" };
const subTabBtn: React.CSSProperties = { border: "none", cursor: "pointer", padding: "0.4rem 0.8rem", borderRadius: "0.5rem", fontSize: "0.8rem", display: "flex", alignItems: "center" };
const themeBtn: React.CSSProperties = { background: "var(--muted)", border: "1px solid var(--border)", borderRadius: "0.5rem", padding: "0.3rem 0.6rem", cursor: "pointer", fontSize: "1rem" };
const cardStyle: React.CSSProperties = { border: "1px solid var(--border)", borderRadius: "0.75rem", padding: "1.5rem", background: "var(--card)" };
const h2Style: React.CSSProperties = { fontSize: "1.25rem", fontWeight: 600, margin: "0 0 1rem" };
const labelStyle: React.CSSProperties = { display: "block", fontSize: "0.75rem", fontWeight: 500, color: "var(--muted-fg)", marginBottom: "0.25rem" };
const inputStyle: React.CSSProperties = { display: "block", width: "100%", background: "var(--bg)", color: "var(--fg)", border: "1px solid var(--border)", borderRadius: "0.375rem", padding: "0.5rem 0.75rem", fontSize: "0.875rem" };
const fieldStyle: React.CSSProperties = { marginBottom: "1rem" };
const hintStyle: React.CSSProperties = { fontSize: "0.7rem", color: "var(--muted-fg)", margin: "0.25rem 0 0" };
const primaryBtn: React.CSSProperties = { background: "var(--accent)", color: "var(--accent-fg)", border: "1px solid var(--accent)", borderRadius: "0.5rem", padding: "0.5rem 1rem", fontSize: "0.875rem", fontWeight: 500, cursor: "pointer" };
const secondaryBtn: React.CSSProperties = { background: "transparent", color: "var(--fg)", border: "1px solid var(--border)", borderRadius: "0.5rem", padding: "0.5rem 1rem", fontSize: "0.875rem", cursor: "pointer" };
const smallBtn: React.CSSProperties = { background: "transparent", border: "1px solid var(--border)", borderRadius: "0.375rem", padding: "0.25rem 0.6rem", fontSize: "0.75rem", cursor: "pointer", color: "var(--muted-fg)" };
const msgStyle: React.CSSProperties = { padding: "0.6rem 0.8rem", borderRadius: "0.5rem", fontSize: "0.8rem", marginBottom: "1rem" };
const errorBannerStyle: React.CSSProperties = { background: "rgba(239,68,68,0.1)", color: "var(--danger)", padding: "0.5rem 0.75rem", borderRadius: "0.375rem", fontSize: "0.75rem", marginBottom: "0.5rem" };
const messagesContainerStyle: React.CSSProperties = { flex: 1, overflowY: "auto", border: "1px solid var(--border)", borderRadius: "0.75rem", background: "var(--card)", padding: "1rem", display: "flex", flexDirection: "column", gap: "0.5rem" };
const textareaStyle: React.CSSProperties = { flex: 1, background: "var(--card)", color: "var(--fg)", border: "1px solid var(--border)", borderRadius: "0.5rem", padding: "0.5rem 0.75rem", fontSize: "0.875rem", resize: "none" };
const sendBtn: React.CSSProperties = { background: "var(--accent)", color: "var(--accent-fg)", border: "1px solid var(--accent)", borderRadius: "0.5rem", padding: "0 1rem", fontSize: "1.2rem", cursor: "pointer" };

function bubbleStyle(role: string, pending?: boolean, error?: boolean): React.CSSProperties {
  const isUser = role === "user";
  return {
    background: error ? "rgba(239,68,68,0.1)" : isUser ? "#3b82f6" : "var(--muted)",
    color: error ? "var(--danger)" : isUser ? "#fff" : "var(--fg)",
    padding: "0.6rem 0.875rem", borderRadius: "0.75rem", fontSize: "0.875rem", lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word",
  };
}
