# ASR (Speech-to-Text) — Known Limitations

**Status:** 2026-10-17 — Audio transcription is **not operational** in
production due to external provider failures. Text and Vision LLM
capabilities work correctly. Audio capability is disabled until external
providers recover.

## NVIDIA Riva ASR (NVCF)

### Status: BLOCKED — External NVIDIA Cloud Functions outage

The AGENTE-GENERAL daemon uses NVIDIA Riva ASR via NVCF (NVIDIA Cloud
Functions) for transcribing WhatsApp voice notes. As of 2026-10-17, all
9 active NVCF ASR deployments tested return errors:

| NVCF deployment ID | Model | HTTP status | Latency |
|---|---|---|---|
| `a9eeee8f-b509-4712-b19d-194361fa5f31` | ai-parakeet-ctc-0_6b-es | 504 Gateway Timeout | 62.3s |
| `1598d209-5e27-4d3c-8079-4751568b1081` | ai-parakeet-ctc-1_1b-asr | 404 "Model not found for language es-US" | 1.2s |
| `d8dd4e9b-fbf5-4fb0-9dba-8cf436c8d965` | ai-parakeet-ctc-0_6b-asr | 500 Internal Server Error | 1.3s |
| `b702f636-f60c-4a3d-a6f4-f3568c13bd7d` | ai-whisper-large-v3 | 500 Internal Server Error | 1.3s |
| `71203149-d3b7-4460-8231-1be2543a1fca` | ai-parakeet-1_1b-rnnt-multilingual-asr | 500 Internal Server Error | 1.2s |
| `d3fe9151-442b-4204-a70d-5fcc597fd610` | ai-parakeet-tdt-0_6b-v2 | 500 Internal Server Error | 1.2s |
| `2b940e91-a70e-4483-a958-d50408b96589` | ai-parakeet-tdt-0_6b | 404 "Model not found for language es-US" | 1.2s |
| `b0e8b4a5-217c-40b7-9b96-17d84e666317` | ai-canary-1b-asr | 500 Internal Server Error | 1.3s |
| `22164014-a6cc-4a6f-b048-f3a303e745bb` | ai-parakeet-ctc-riva | 504 Gateway Timeout | 62.4s |

**Cause:** NVIDIA Cloud Functions workers are saturated or down. This is
an external NVIDIA issue, not an AGENTE-GENERAL code defect.

**Resolution:** Wait for NVIDIA to recover, or use a non-NVCF ASR
provider (see "Groq" below).

## Groq Whisper (alternative)

### Status: BLOCKED — Regional block from this environment

The Groq ASR API (`https://api.groq.com/openai/v1/audio/transcriptions`,
model `whisper-large-v3-turbo`) was tested as a fallback. From this
environment (Hong Kong), the Groq API returns HTTP 403 Forbidden for
all endpoints, including `GET /` without an Authorization header. This
indicates a Cloudflare regional block, not an invalid API key.

| Endpoint | Method | HTTP status | Notes |
|---|---|---|---|
| `/openai/v1/models` | GET | 403 Forbidden | Even with valid API key |
| `/openai/v1/chat/completions` | POST | 403 Forbidden | Even with valid API key |
| `/` (root, no auth) | GET | 403 Forbidden | Confirms regional block |

**Cause:** Cloudflare blocks the Hong Kong IP range used by this
container.

**Resolution:** Deploy the backend in a Groq-supported region, or use
another ASR provider. Verify the Groq API key from a different IP
before declaring it invalid.

## Z.ai ASR SDK (fallback of last resort)

The `z-ai-web-dev-sdk` package provides ASR via the `z-ai asr` CLI and
the SDK's `audio.asr.create()` method. Quality tests with a Spanish
audio sample ("Hola, esto es una prueba de transcripción de audio en
español") returned incorrect transcriptions:

```
Expected: "Hola, esto es una prueba de transcripción de audio en español"
Got:      "Hola, as though it's an idea transcribe the audio. I am old."
```

**Status:** Functional but quality insufficient. Not suitable as an
automatic fallback. Documented as the last-resort option when no other
ASR provider is available.

## Decision

- **Text** ✅ NVIDIA NIM `z-ai/glm-5.3-flash` — operational
- **Vision** ✅ NVIDIA NIM `meta/llama-3.2-11b-vision-instruct` — operational
- **Audio** ❌ Not operational — disabled in production until either
  NVIDIA Riva recovers or Groq is verified from a supported region

## Recommended action items

1. **Monitor NVIDIA NVCF status** — re-test the 9 deployments weekly
   until they recover.
2. **Verify Groq from a non-HK IP** — if the API key works from
   another region, deploy the backend there.
3. **Do not enable audio in production** until one ASR provider
   passes a real transcription test in Spanish.
4. **Accept limitation explicitly** — the product owner must sign
   off on shipping without audio support before declaring
   READY FOR PRODUCTION.
