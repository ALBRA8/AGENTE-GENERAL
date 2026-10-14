"use client";

import { Header } from "@/components/header";
import { useEffect, useState } from "react";
import {
  PROVIDER_PRESETS,
  addProvider,
  deleteProvider,
  loadProviders,
  maskApiKey,
  setActiveProvider,
  updateProvider,
  type ProviderConfig,
  type ProviderPreset,
} from "@/lib/providers";

export default function ConfigPage() {
  const [providers, setProviders] = useState<ProviderConfig[]>([]);
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const id = requestAnimationFrame(() => {
      if (!cancelled) setProviders(loadProviders());
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(id);
    };
  }, []);

  const refresh = () => setProviders(loadProviders());

  return (
    <>
      <Header />
      <main style={{ maxWidth: "48rem", margin: "0 auto", padding: "2rem 1rem" }}>
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: "1rem",
            marginBottom: "2rem",
            flexWrap: "wrap",
          }}
        >
          <div>
            <h1
              style={{
                fontSize: "1.5rem",
                fontWeight: 600,
                margin: "0 0 0.25rem",
              }}
            >
              Configuración
            </h1>
            <p
              style={{
                color: "var(--muted-fg)",
                fontSize: "0.875rem",
                margin: 0,
              }}
            >
              Configura los proveedores de LLM. Las claves se guardan solo en
              este navegador y nunca se envían a ningún servidor que no sea el
              proveedor configurado.
            </p>
          </div>
          <button
            onClick={() => setShowForm((v) => !v)}
            style={btnPrimary}
          >
            {showForm ? "Cancelar" : "+ Nuevo proveedor"}
          </button>
        </div>

        {showForm && (
          <ProviderForm
            onSaved={() => {
              setShowForm(false);
              refresh();
            }}
          />
        )}

        <h2
          style={{
            fontSize: "0.875rem",
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "var(--muted-fg)",
            margin: "2rem 0 0.75rem",
          }}
        >
          Proveedores guardados
        </h2>

        {providers.length === 0 ? (
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
            No hay proveedores configurados. Crea uno con el botón de arriba.
          </div>
        ) : (
          <div style={{ display: "grid", gap: "0.75rem" }}>
            {providers.map((p) => (
              <ProviderRow key={p.id} provider={p} onChange={refresh} />
            ))}
          </div>
        )}

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
          <strong>Seguridad:</strong> las claves API se almacenan en
          <code style={{ margin: "0 0.25rem" }}>localStorage</code>
          del navegador. No se envían a ningún servidor intermedio. El
          dashboard llama directamente al endpoint configurado (OpenAI,
          NVIDIA, Anthropic, etc.) usando el SDK OpenAI-compatible.
        </div>
      </main>
    </>
  );
}

function ProviderForm({ onSaved }: { onSaved: () => void }) {
  const [preset, setPreset] = useState<ProviderPreset>(PROVIDER_PRESETS[0]);
  const [label, setLabel] = useState(PROVIDER_PRESETS[0].label);
  const [baseUrl, setBaseUrl] = useState(PROVIDER_PRESETS[0].base_url);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(PROVIDER_PRESETS[0].model);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const applyPreset = (p: ProviderPreset) => {
    setPreset(p);
    setLabel(p.label);
    setBaseUrl(p.base_url);
    setModel(p.model);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!baseUrl.trim() && preset.kind !== "ollama") {
      setError("La URL base es obligatoria.");
      return;
    }
    if (preset.api_key_required && !apiKey.trim()) {
      setError("La API key es obligatoria para este proveedor.");
      return;
    }
    if (!model.trim()) {
      setError("El modelo es obligatorio.");
      return;
    }

    setSaving(true);
    addProvider({
      label: label.trim() || preset.label,
      kind: preset.kind,
      base_url: baseUrl.trim() || preset.base_url,
      api_key: apiKey.trim() || "ollama",
      model: model.trim(),
    });
    setSaving(false);
    setApiKey("");
    onSaved();
  };

  return (
    <form
      onSubmit={handleSubmit}
      style={{
        border: "1px solid var(--border)",
        borderRadius: "0.75rem",
        padding: "1.5rem",
        background: "var(--card)",
        display: "grid",
        gap: "1rem",
      }}
    >
      <div>
        <label style={labelStyle}>Plantilla</label>
        <select
          value={preset.kind}
          onChange={(e) => {
            const found = PROVIDER_PRESETS.find(
              (p) => p.kind === e.target.value,
            );
            if (found) applyPreset(found);
          }}
          style={inputStyle}
        >
          {PROVIDER_PRESETS.map((p) => (
            <option key={p.kind} value={p.kind}>
              {p.label}
            </option>
          ))}
        </select>
        <p style={hintStyle}>{preset.hint}</p>
      </div>

      <div>
        <label style={labelStyle}>Etiqueta (opcional)</label>
        <input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="ej. NVIDIA GLM-5.3"
          style={inputStyle}
        />
      </div>

      <div>
        <label style={labelStyle}>URL base</label>
        <input
          type="url"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://integrate.api.nvidia.com/v1"
          style={inputStyle}
          required={preset.kind !== "ollama"}
        />
      </div>

      <div>
        <label style={labelStyle}>API key</label>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={preset.api_key_required ? "nvapi-..." : "ollama (ignorada)"}
          style={inputStyle}
          required={preset.api_key_required}
          autoComplete="off"
        />
      </div>

      <div>
        <label style={labelStyle}>Modelo</label>
        <input
          type="text"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="z-ai/glm-5.3"
          style={inputStyle}
          required
        />
      </div>

      {error && (
        <div
          style={{
            color: "#ef4444",
            fontSize: "0.875rem",
            padding: "0.5rem 0.75rem",
            background: "rgba(239, 68, 68, 0.08)",
            borderRadius: "0.375rem",
          }}
        >
          {error}
        </div>
      )}

      <div style={{ display: "flex", gap: "0.5rem" }}>
        <button type="submit" disabled={saving} style={btnPrimary}>
          {saving ? "Guardando..." : "Guardar proveedor"}
        </button>
      </div>
    </form>
  );
}

function ProviderRow({
  provider,
  onChange,
}: {
  provider: ProviderConfig;
  onChange: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(provider.model);

  const handleSave = () => {
    updateProvider(provider.id, {
      base_url: baseUrl.trim(),
      model: model.trim(),
      ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
    });
    setEditing(false);
    setApiKey("");
    onChange();
  };

  const handleActivate = () => {
    setActiveProvider(provider.id);
    onChange();
  };

  const handleDelete = () => {
    if (window.confirm(`¿Eliminar el proveedor "${provider.label}"?`)) {
      deleteProvider(provider.id);
      onChange();
    }
  };

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "0.75rem",
        padding: "1rem 1.25rem",
        background: "var(--card)",
        display: "flex",
        flexDirection: "column",
        gap: "0.75rem",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "0.5rem",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span
            style={{
              display: "inline-block",
              width: "0.5rem",
              height: "0.5rem",
              borderRadius: "9999px",
              background: provider.active ? "#10b981" : "var(--muted-fg)",
            }}
          />
          <strong style={{ fontSize: "0.95rem" }}>{provider.label}</strong>
          <span
            style={{
              fontSize: "0.7rem",
              padding: "0.1rem 0.4rem",
              borderRadius: "0.25rem",
              background: "var(--muted)",
              color: "var(--muted-fg)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            {provider.kind}
          </span>
        </div>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          {!provider.active && (
            <button onClick={handleActivate} style={btnSmall}>
              Activar
            </button>
          )}
          <button
            onClick={() => setEditing((v) => !v)}
            style={btnSmall}
          >
            {editing ? "Cancelar" : "Editar"}
          </button>
          <button
            onClick={handleDelete}
            style={{ ...btnSmall, color: "#ef4444" }}
          >
            Eliminar
          </button>
        </div>
      </div>

      {!editing ? (
        <div
          style={{
            fontSize: "0.8rem",
            color: "var(--muted-fg)",
            display: "grid",
            gap: "0.25rem",
          }}
        >
          <div>
            <strong>URL:</strong> <code>{provider.base_url || "—"}</code>
          </div>
          <div>
            <strong>Modelo:</strong> <code>{provider.model}</code>
          </div>
          <div>
            <strong>API key:</strong>{" "}
            <code>{maskApiKey(provider.api_key)}</code>
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gap: "0.75rem" }}>
          <div>
            <label style={labelStyle}>URL base</label>
            <input
              type="url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>API key (dejar vacío para mantener)</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="••••••••"
              style={inputStyle}
              autoComplete="off"
            />
          </div>
          <div>
            <label style={labelStyle}>Modelo</label>
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              style={inputStyle}
            />
          </div>
          <button onClick={handleSave} style={btnPrimary}>
            Guardar cambios
          </button>
        </div>
      )}
    </div>
  );
}

const btnPrimary: React.CSSProperties = {
  background: "var(--accent)",
  color: "var(--accent-fg)",
  border: "1px solid var(--accent)",
  borderRadius: "0.5rem",
  padding: "0.5rem 1rem",
  fontSize: "0.875rem",
  fontWeight: 500,
  cursor: "pointer",
};

const btnSmall: React.CSSProperties = {
  background: "transparent",
  color: "var(--fg)",
  border: "1px solid var(--border)",
  borderRadius: "0.375rem",
  padding: "0.25rem 0.6rem",
  fontSize: "0.75rem",
  cursor: "pointer",
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
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
};

const hintStyle: React.CSSProperties = {
  fontSize: "0.7rem",
  color: "var(--muted-fg)",
  margin: "0.25rem 0 0",
};
