# Multimodal and messaging integration status

This document distinguishes reusable provider support from a complete user-facing
integration. A serializer test passing does not prove that WhatsApp, Telegram,
the dashboard, or a deployed service can receive and process media end to end.

## Implemented in this branch

- OpenAI-compatible provider serialization can encode user messages with
  `metadata.image_data_urls` as text plus image blocks.
- Anthropic serialization supports the equivalent native base64 image blocks.
- Only base64 data URLs for PNG, JPEG, WebP, and GIF are accepted. Arbitrary
  remote URLs and local paths are rejected to avoid provider-side URL fetching.
- Image count is limited to eight per message and each encoded attachment is
  capped at 8 MiB.
- Unit tests cover image serialization, invalid remote URLs, and attachment count.

## Not yet complete / not verified

- No end-to-end image upload endpoint currently converts incoming WhatsApp or
  dashboard uploads into `image_data_urls`.
- The provider/model must support vision; serializer support alone cannot add
  vision capability to a text-only model.
- No complete dashboard chat composer exists yet for text, audio, and images.
- This repository branch does not currently contain WhatsApp or Telegram
  transport adapters. The CRM repository has separate channel code; it must be
  integrated deliberately rather than assumed to be part of this runtime.
- Audio decoding is currently an optional adapter contract, not a production
  end-to-end transcription service. The generic VoiceAdapter expects an ASR
  module exposing a compatible `transcribe(bytes)` function, which must be
  verified for the chosen engine before claiming live audio support.
- Production reconnection, webhook retries, message idempotency, delivery
  receipts, and duplicate-response prevention remain channel-specific work.

## Next acceptance gates

1. Add a protected, bounded media-ingest endpoint that validates MIME type,
   file signatures, dimensions, encoded size, and authorization before producing
   internal image data URLs.
2. Add a vision-capable model setting and fail clearly if the selected model
   does not support images.
3. Add dashboard chat with text/image attachments and safe error/status states.
4. Implement an ASR adapter with a documented engine API and fixture-based tests.
5. Integrate WhatsApp/Telegram via explicit transport adapters with durable
   deduplication keys, retry/backoff, timeouts, and structured health diagnostics.
6. Add integration tests with mocked provider/channel APIs, then run real staging
   acceptance tests before declaring any channel reliable.

Never put provider keys or channel tokens in source control. Store them in the
existing encrypted settings/secrets mechanism or deployment secret manager.
