# Third-Party Notices

This file acknowledges third-party code adapted into AGENTE-GENERAL.

## OpenClaw WhatsApp Extension

- **Project:** OpenClaw
- **Repository:** https://github.com/openclaw/openclaw
- **Subdirectory:** `extensions/whatsapp/src/`
- **License:** MIT License
- **Copyright:** © OpenClaw Foundation

The following utilities in `dashboard/src/server/openclaw-adapted.js` were
adapted from OpenClaw's WhatsApp extension (`extensions/whatsapp/src/`).
The TypeScript implementations were rewritten as JavaScript (CommonJS)
for the AGENTE-GENERAL WhatsApp daemon. The architectural patterns and
core algorithms are preserved:

| Adapted file (AGENTE-GENERAL) | Original (OpenClaw) | What was adapted |
|---|---|---|
| `dashboard/src/server/openclaw-adapted.js` → `resolveSessionDir()` | `extensions/whatsapp/src/accounts.ts` | Persistent session directory resolution (replaces `/tmp/wa-session` ephemeral) |
| `dashboard/src/server/openclaw-adapted.js` → `writeCredsAtomically()` | `extensions/whatsapp/src/creds-persistence.ts` → `writeCredsJsonAtomically()`, `replaceFileAtomic()` | Atomic credential writes with `.bak` backup and mode `0o600` |
| `dashboard/src/server/openclaw-adapted.js` → `restoreCredsFromBackupIfNeeded()` | `extensions/whatsapp/src/creds-persistence.ts` → `restoreCredsFromBackupIfNeeded()` | Recovery from corrupt `creds.json` via backup file |
| `dashboard/src/server/openclaw-adapted.js` → `DEFAULT_RECONNECT_POLICY`, `resolveReconnectPolicy()`, `computeBackoff()` | `extensions/whatsapp/src/reconnect.ts` → `DEFAULT_RECONNECT_POLICY`, `resolveReconnectPolicy()`, `computeBackoff()` | Reconnect policy: `initialMs=2s`, `maxMs=30s`, `factor=1.8`, `jitter=0.25`, `maxAttempts=12` |
| `dashboard/src/server/openclaw-adapted.js` → `createWatchdog()` | `extensions/whatsapp/src/connection-controller.ts` → `startTimers()`, `lastTransportActivityAt`, `lastInboundAt`, `appSilenceTimeoutMs = messageTimeoutMs*4`, `openedAfterRecentInbound` | Dual watchdog (transport-level WS frames + app-level inbound messages) with 4× grace window post-reconnect |
| `dashboard/src/server/openclaw-adapted.js` → `acquireConnectionOwnerLock()` | `extensions/whatsapp/src/connection-owner.ts` → `acquireWhatsAppGatewayConnectionOwner()`, `acquireOwnerLease()` | Cross-process file lock per `authDir` (5 min stale recovery) |
| `dashboard/src/server/openclaw-adapted.js` → `WhatsAppConnectionOwnerBusyError` | `extensions/whatsapp/src/connection-owner.ts` → `WhatsAppConnectionOwnerBusyError` | Typed error for lock contention |
| `dashboard/src/server/openclaw-adapted.js` → `createDurableMessageId()`, `createPersistentDedupStore()` | `extensions/whatsapp/src/inbound/durable-receive.ts` → `createWhatsAppDurableInboundMessageId()`, `createPersistentDedupeCache()` | Persistent message deduplication: `sha256(remoteJid+msgId)`, 7-day TTL, 5000 entries, auto-disable on failure |

### MIT License (OpenClaw)

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Other dependencies

All other third-party dependencies (Baileys, FastAPI, Next.js, etc.) are
declared in their respective `package.json` / `pyproject.toml` files and
retain their original licenses.
