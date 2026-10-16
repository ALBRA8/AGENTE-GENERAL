# NVIDIA NIM Provider Integration

## Overview

ALBRA SEED now supports NVIDIA NIM (NVIDIA Inference Microservices) as
an LLM provider via the OpenAI-compatible endpoint at
`https://integrate.api.nvidia.com/v1`.

The NVIDIA provider uses the `openai` Python SDK pointed at NVIDIA's
endpoint, exactly like the existing ZAI/Groq/Ollama providers. It
implements the same `LLMProvider` interface and is registered in the
`ProviderRegistry` alongside the existing providers.

## Configuration

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `NVIDIA_API_KEY` | Yes (for NVIDIA provider) | NVIDIA API key (starts with `nvapi-`) |
| `SETTINGS_ENCRYPTION_KEY` | Yes (for dashboard persistence) | Fernet master key for encrypting API keys at rest |
| `ALBRA_PROVIDER_CONFIG_DB` | No | Path to the SQLite config DB (default: `./data/provider_config.db`) |

### Generating SETTINGS_ENCRYPTION_KEY

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

This key MUST be set as an environment variable in Render (or your
deployment platform). Without it, the backend cannot persist API keys
and will fail closed with a clear error message.

### Default NVIDIA config

- **Base URL:** `https://integrate.api.nvidia.com/v1`
- **Model:** `z-ai/glm-5.3`
- **Temperature:** 0.5
- **top_p:** 1.0
- **max_tokens:** 1024
- **Timeout:** 60s
- **Stream:** False (default)
- **max_retries:** 2 (for transient errors only)

## Using Mock (no credentials)

The Mock provider is always available and requires no configuration.
It produces deterministic responses and is used for testing and
CORE_ONLY mode.

To use Mock: in the dashboard, select "Mock" as the provider and save.

## Dashboard configuration

1. Open the dashboard (served by the Next.js app in `dashboard/`).
2. Go to the configuration page.
3. Select "NVIDIA NIM" as the provider.
4. Enter the base URL (default: `https://integrate.api.nvidia.com/v1`).
5. Enter the model (default: `z-ai/glm-5.3`).
6. Enter the API key (starts with `nvapi-`).
7. Adjust temperature, top_p, max_tokens, timeout as needed.
8. Click "Guardar".
9. Click "Probar conexión" to verify.

## Testing the connection

The "Probar conexión" button calls `POST /api/v1/provider/test`, which:
- Validates the provider, model, and API key are configured.
- Makes a minimal 1-token completion request to the provider.
- Returns `ok: true` with latency, or `ok: false` with a safe error
  message (no secrets leaked).
- Records the test result (`last_test_at`, `last_test_ok`) in the DB.

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/provider/config` | Read non-sensitive config (no API key) |
| PUT | `/api/v1/provider/config` | Save non-sensitive config |
| PUT | `/api/v1/provider/api-key` | Save/replace the API key (encrypted) |
| DELETE | `/api/v1/provider/api-key` | Clear the API key |
| POST | `/api/v1/provider/test` | Test the connection |
| POST | `/api/v1/provider/reset` | Reset to Mock defaults |
| GET | `/api/v1/provider/defaults` | Get default configs per provider |

## Security

- The API key is encrypted at rest with Fernet (authenticated symmetric
  encryption) using `SETTINGS_ENCRYPTION_KEY`.
- The API key is NEVER returned by any read endpoint — only
  `api_key_set: bool`.
- The API key is NEVER logged, NEVER included in error messages, and
  NEVER sent to any endpoint other than the configured `base_url`.
- Error messages are sanitized to strip any accidental key leakage.
- Auth errors (401/403) are NOT retried. Transient errors (5xx, timeout,
  429) are retried with exponential backoff up to `max_retries`.
- If `SETTINGS_ENCRYPTION_KEY` is missing, the store fails closed:
  `save_config()` raises, `save_api_key()` raises.

## Error handling

| Error type | Behavior |
|---|---|
| Missing API key | `NotImplementedError` at provider construction |
| Invalid API key (401) | Error response, NO retry |
| Timeout | Error response, retried up to `max_retries` |
| Rate limit (429) | Error response, retried up to `max_retries` |
| Server error (5xx) | Error response, retried up to `max_retries` |
| Validation error (400) | Error response, NO retry |
| Empty response | AgentResponse with empty content |
| Network error | Error response, retried |

## Running tests

```bash
# NVIDIA provider tests (mock-based, no real API calls)
python -m pytest tests/unit/test_nvidia_provider.py -v

# Settings store + endpoint tests
python -m pytest tests/security/test_provider_settings.py -v

# Full suite (excluding LLM-real tests)
python -m pytest tests/ -q --ignore=tests/llm_real
```

All tests use mocks — NO real NVIDIA API calls are made during the test
suite.

## Deploying to Render

1. Set `SETTINGS_ENCRYPTION_KEY` in Render's environment variables
   (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
2. Optionally set `ALBRA_PROVIDER_CONFIG_DB` to a path on a persistent
   disk (e.g. `/data/provider_config.db`). If using Render's ephemeral
   filesystem, the config will NOT survive redeploys.
3. Install the `cryptography` package: add `cryptography>=42.0.0` to
   Render's build requirements, or install with
   `pip install 'albra-seed[crypto]'`.
4. The startup command remains unchanged:
   `uvicorn render_app:app --host 0.0.0.0 --port $PORT`

## What happens when the provider is down

- The provider's `complete()` method returns an `AgentResponse` with
  `errors` populated (after exhausting retries for transient errors).
- The `health()` method returns `False`.
- The test-connection endpoint returns `ok: false` with a safe message.
- The runtime does NOT crash — it falls back to the Mock provider if
  configured in the `ProviderRouter`.
- No automatic retry of the failed request — the caller must handle
  the error.

## What is NOT done

- The dashboard does NOT have authentication — anyone with network
  access can read/write the provider config. This MUST be protected by
  a reverse proxy with authentication before production use.
- The provider config is stored in SQLite, which is single-writer. All
  workers must share the same DB file on the same host.
- The NVIDIA provider does NOT support streaming tool calls — callers
  that need tool calls should use `stream=False`.
- The dashboard is a standalone Next.js app; it is NOT served by the
  ALBRA SEED backend. Deploy it separately (e.g. on Vercel or Render
  static site).
