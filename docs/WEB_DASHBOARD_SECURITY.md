# Browser dashboard and provider-secret security

## Current repository scope

The `AGENTE-GENERAL` repository contains the Python seed/API but no tracked
web dashboard, frontend source, or assets for the reported dashboard on
`localhost:9000`. Therefore a code change in this repository cannot remove
browser-side `localStorage` usage from that separate application. This is an
explicit integration blocker, not a passed security control.

Until the dashboard source is included and audited, **do not enter real
provider credentials into its browser UI**. If keys were previously saved in
browser storage, remove them from that origin's site data and rotate/revoke
those keys if they may have been exposed.

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
environment with a long, randomly generated secret before enabling the
dashboard. Do not place this value in `NEXT_PUBLIC_*`, a frontend bundle,
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
