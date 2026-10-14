"use client";

import { Header } from "@/components/header";
import { useEffect, useRef, useState } from "react";
import { getActiveProvider, type ProviderConfig } from "@/lib/providers";

interface Message {
  role: "system" | "user" | "assistant";
  content: string;
}

export default function ChatPage() {
  const [provider, setProvider] = useState<ProviderConfig | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("You are a helpful assistant.");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [temperature, setTemperature] = useState(0.5);
  const [maxTokens, setMaxTokens] = useState(1024);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setProvider(getActiveProvider());
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, loading]);

  const send = async () => {
    if (!input.trim() || loading) return;
    if (!provider) {
      setError("No hay proveedor activo. Configura uno en /config.");
      return;
    }

    const userMsg: Message = { role: "user", content: input.trim() };
    const newMessages: Message[] = [
      ...(systemPrompt.trim() ? [{ role: "system" as const, content: systemPrompt.trim() }] : []),
      ...messages,
      userMsg,
    ];
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setError(null);
    setLoading(true);

    try {
      // Direct OpenAI-compatible call from the browser to the configured
      // provider endpoint. The API key is sent only to that endpoint.
      const res = await fetch(`${provider.base_url}/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${provider.api_key}`,
        },
        body: JSON.stringify({
          model: provider.model,
          messages: newMessages.map((m) => ({ role: m.role, content: m.content })),
          temperature,
          top_p: 1,
          max_tokens: maxTokens,
          stream: false,
        }),
        signal: AbortSignal.timeout(60000),
      });

      if (!res.ok) {
        const errText = await res.text().catch(() => "unknown error");
        throw new Error(`HTTP ${res.status}: ${errText.slice(0, 300)}`);
      }

      const data = await res.json();
      const assistantContent: string =
        data?.choices?.[0]?.message?.content || "(empty response)";

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: assistantContent },
      ]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "error desconocido";
      setError(msg);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `❌ Error: ${msg}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  if (!provider) {
    return (
      <>
        <Header />
        <main style={{ maxWidth: "48rem", margin: "0 auto", padding: "2rem 1rem" }}>
          <h1 style={{ fontSize: "1.5rem", fontWeight: 600, marginBottom: "0.5rem" }}>
            Chat
          </h1>
          <div
            style={{
              border: "1px dashed var(--border)",
              borderRadius: "0.75rem",
              padding: "2.5rem 1rem",
              textAlign: "center",
              color: "var(--muted-fg)",
              fontSize: "0.875rem",
            }}
          >
            No hay proveedor activo.
            <br />
            <a
              href="/config"
              style={{
                color: "var(--fg)",
                textDecoration: "underline",
                fontWeight: 500,
              }}
            >
              Configura uno aquí →
            </a>
          </div>
        </main>
      </>
    );
  }

  return (
    <>
      <Header />
      <main
        style={{
          maxWidth: "48rem",
          margin: "0 auto",
          padding: "1.5rem 1rem",
          display: "flex",
          flexDirection: "column",
          height: "calc(100vh - 4rem)",
        }}
      >
        <div style={{ marginBottom: "1rem" }}>
          <h1 style={{ fontSize: "1.25rem", fontWeight: 600, margin: 0 }}>
            Chat
          </h1>
          <p
            style={{
              fontSize: "0.75rem",
              color: "var(--muted-fg)",
              margin: "0.25rem 0 0",
            }}
          >
            Proveedor activo:{" "}
            <strong>{provider.label}</strong> · {provider.model} ·{" "}
            <code style={{ fontSize: "0.7rem" }}>{provider.base_url}</code>
          </p>
        </div>

        {/* System prompt + params */}
        <details
          style={{
            marginBottom: "0.75rem",
            border: "1px solid var(--border)",
            borderRadius: "0.5rem",
            padding: "0.5rem 0.75rem",
            background: "var(--card)",
            fontSize: "0.8rem",
          }}
        >
          <summary style={{ cursor: "pointer", color: "var(--muted-fg)" }}>
            Parámetros avanzados
          </summary>
          <div
            style={{
              display: "grid",
              gap: "0.75rem",
              marginTop: "0.75rem",
            }}
          >
            <div>
              <label style={labelStyle}>System prompt</label>
              <textarea
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                rows={2}
                style={{ ...inputStyle, fontFamily: "inherit" }}
                placeholder="You are a helpful assistant."
              />
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "0.75rem",
              }}
            >
              <div>
                <label style={labelStyle}>
                  Temperature: {temperature.toFixed(2)}
                </label>
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
                <label style={labelStyle}>Max tokens: {maxTokens}</label>
                <input
                  type="number"
                  min="1"
                  max="32768"
                  value={maxTokens}
                  onChange={(e) => setMaxTokens(parseInt(e.target.value) || 1024)}
                  style={inputStyle}
                />
              </div>
            </div>
          </div>
        </details>

        {/* Messages */}
        <div
          ref={scrollRef}
          style={{
            flex: 1,
            overflowY: "auto",
            border: "1px solid var(--border)",
            borderRadius: "0.75rem",
            background: "var(--card)",
            padding: "1rem",
            marginBottom: "0.75rem",
            display: "flex",
            flexDirection: "column",
            gap: "0.75rem",
          }}
        >
          {messages.length === 0 && !loading && (
            <div
              style={{
                textAlign: "center",
                color: "var(--muted-fg)",
                fontSize: "0.875rem",
                marginTop: "2rem",
              }}
            >
              Escribe un mensaje para empezar.
            </div>
          )}
          {messages.map((m, i) => (
            <MessageBubble key={i} message={m} />
          ))}
          {loading && (
            <div
              style={{
                fontSize: "0.875rem",
                color: "var(--muted-fg)",
                fontStyle: "italic",
              }}
            >
              ● ● ●
            </div>
          )}
        </div>

        {error && (
          <div
            style={{
              color: "#ef4444",
              fontSize: "0.75rem",
              padding: "0.5rem 0.75rem",
              background: "rgba(239, 68, 68, 0.08)",
              borderRadius: "0.375rem",
              marginBottom: "0.75rem",
              fontFamily: "ui-monospace, monospace",
            }}
          >
            {error}
          </div>
        )}

        {/* Input */}
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Escribe un mensaje... (Enter para enviar, Shift+Enter para nueva línea)"
            rows={2}
            style={{
              flex: 1,
              background: "var(--card)",
              color: "var(--fg)",
              border: "1px solid var(--border)",
              borderRadius: "0.5rem",
              padding: "0.6rem 0.8rem",
              fontSize: "0.875rem",
              fontFamily: "inherit",
              resize: "none",
            }}
          />
          <button
            onClick={send}
            disabled={loading || !input.trim()}
            style={{
              ...btnPrimary,
              padding: "0 1.25rem",
              opacity: loading || !input.trim() ? 0.5 : 1,
            }}
          >
            {loading ? "..." : "Enviar"}
          </button>
        </div>
      </main>
    </>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const isError = message.content.startsWith("❌");

  return (
    <div
      style={{
        alignSelf: isUser ? "flex-end" : "flex-start",
        maxWidth: "85%",
        background: isError
          ? "rgba(239, 68, 68, 0.08)"
          : isUser
            ? "var(--accent)"
            : "var(--muted)",
        color: isError
          ? "#ef4444"
          : isUser
            ? "var(--accent-fg)"
            : "var(--fg)",
        padding: "0.6rem 0.875rem",
        borderRadius: "0.75rem",
        fontSize: "0.875rem",
        lineHeight: 1.5,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    >
      {message.content}
    </div>
  );
}

const btnPrimary: React.CSSProperties = {
  background: "var(--accent)",
  color: "var(--accent-fg)",
  border: "1px solid var(--accent)",
  borderRadius: "0.5rem",
  fontSize: "0.875rem",
  fontWeight: 500,
  cursor: "pointer",
};

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: "0.7rem",
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
  padding: "0.4rem 0.6rem",
  fontSize: "0.8rem",
};
