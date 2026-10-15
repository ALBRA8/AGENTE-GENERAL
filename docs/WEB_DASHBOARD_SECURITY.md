# Browser dashboard and provider-secret security

## Current repository scope

The provider-configuration dashboard is tracked under `dashboard/` in the
provider-settings feature branch. This review covers that page and its
provider-settings API integration only; it does not certify a separate app
running on `localhost:9000`, the deployed service, or the full frontend build.

Do not enter real provider credentials into any unreviewed dashboard. If keys
were previously saved in browser storage, remove them from that origin's site
data and rotate/revoke those keys if they may have been exposed.

## Required architecture

1. Provider credentials live on the backend only: inject them through the
   backend process environment or a supported server-side secrets manager.
   Supported environment names include `OPENAI_API_KEY`,
   `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, and `ZAI_API_KEY`.
2. Construct and call provider SDKs from the backend. The browser sends the
   user message and non-secret options to the backend API; it must not call
   provider APIs directly using a user's long-lived API key.
3. Never store provider keys in `localStorage`, `sessionStorage`, IndexedDB,
   URLs, frontend bundles, client-side state persisted between sessions, or
   browser logs. Do not return secret values from a backend endpoint.
4. Do not create a generic unauthenticated endpoint that accepts and persists
   provider keys. If temporary credential entry is ever required, design it
   separately with explicit authentication, strict origin/CSRF controls,
   memory-only handling, no logging, and a documented lifetime.
5. For local development, bind the backend to loopback unless remote access is
   intentionally configured. If the UI is exposed beyond loopback, require
   authentication, restrict CORS to the exact UI origin, and use TLS.
6. Treat a provider key entered into a browser as compromised if any page
   script, extension, XSS, browser profile sync, or debugging tool could read
   it. Rotate it; deleting browser storage alone does not revoke it.

## Acceptance checks when the dashboard source is available

- Static scan finds no provider key persistence in browser storage.
- Browser network inspection shows chat requests go only to the configured
  backend, not directly to OpenAI/Anthropic/Groq/Z.ai.
- Backend provider configuration uses environment variables or a server-side
  secrets manager and never returns secret values.
- Tests prove keys do not appear in application logs, errors, API responses,
  traces, browser storage, or built frontend assets.
- Run the complete dashboard test/build suite and repeat the checks against
  the actual local dashboard, not just the Python seed repository.

These checks must be recorded as **NOT VERIFIED** until run against the actual
dashboard code and local services. The Python seed CI does not certify a
separate frontend or a running machine.


## Provider settings API authentication

The provider settings endpoints (`/api/v1/provider/*`) require a server-side
bearer token. Configure `ALBRA_PROVIDER_SETTINGS_TOKEN` in the API process
environment with a randomly generated secret of at least 32 characters before enabling the
dashboard. The API fails closed with HTTP 503 if the configured value is shorter. Do not place this value in `NEXT_PUBLIC_*`, a frontend bundle,
source control, a URL, or browser storage.

Generate a token locally, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set the resulting value in the backend's secret/environment configuration.
The dashboard's token field sends it as an `Authorization: Bearer` header and
keeps it in component memory only; re-enter it after reloading the page. The
backend compares the supplied value in constant time. If the environment
variable is missing, the endpoints fail closed with HTTP 503; missing or
incorrect credentials receive HTTP 401. This token protects the provider
settings endpoints only; it is not a replacement for authentication and
authorization on other API routes.

Use HTTPS for any non-loopback deployment, restrict network access and CORS
to the intended dashboard origin, and rotate the token if exposed. Keep
`SETTINGS_ENCRYPTION_KEY` configured separately for encryption of stored
provider API keys. Never reuse the provider API key as the dashboard access
token.


## Hardening implemented in the security branch

- Validation responses omit Pydantic's raw `input` field; oversized submitted secrets are not echoed.
- Encryption and database error logs record exception classes, not exception messages that may contain paths or secret-bearing values.
- CORS is disabled unless `ALBRA_CORS_ORIGINS` is explicitly configured with comma-separated exact origins (scheme + host + optional port). Credentials are disabled; only the API methods and headers needed by the dashboard are allowed.
- Request bodies with a declared `Content-Length` above 1 MiB are rejected. API key fields are separately limited to 4096 characters.

## Mandatory deployment controls (not verifiable from repository source alone)

- Set `ALBRA_CORS_ORIGINS` to the exact deployed dashboard origin(s), never `*`; verify the browser preflight from the real origin.
- Terminate TLS at the managed platform or reverse proxy, redirect HTTP to HTTPS, and configure forwarded headers only from trusted proxy hops. Do not trust arbitrary client-supplied `X-Forwarded-For` or `X-Forwarded-Proto`.
- Apply shared/distributed rate limits at the edge for authentication failures and provider-settings writes. The app's request-size cap is not a rate limiter and must not be treated as one.
- Keep `ALBRA_PROVIDER_SETTINGS_TOKEN` and `SETTINGS_ENCRYPTION_KEY` in the platform secret manager, independently generated and rotated. Restrict who can read them; back up the encryption key separately from the encrypted database and rehearse recovery.
- Confirm the platform's SQLite disk persistence, file permissions, TLS certificate, proxy configuration, and isolation between tenants/users. The current settings store is a single shared configuration store, not per-user/tenant storage.

Do not mark production readiness complete until these deployment checks and the dashboard build/security scan have passed in CI and the deployed environment.
