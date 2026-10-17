/**
 * OpenClaw-adapted utilities for the WhatsApp daemon.
 *
 * Adapted from OpenClaw WhatsApp extension (MIT-licensed).
 * Source: https://github.com/openclaw/openclaw/tree/main/extensions/whatsapp/src
 *
 * Original copyright (c) OpenClaw Foundation.
 *
 * Provides:
 *   - Persistent session directory resolution (replaces /tmp/wa-session ephemeral)
 *   - Atomic credential writes with .bak backup + 0o600 file mode
 *   - Backup restoration on corruption
 *   - Reconnect policy with exponential backoff + 25% jitter + maxAttempts 12
 *   - Dual watchdog timers (transport-level WS frames + app-level inbound messages)
 *   - Cross-process file lock (Linux flock / macOS fcntl) per auth directory
 *   - Persistent message deduplication (sha256(jid+msgId), 7-day TTL, 5000 entries)
 *
 * Adapted from TypeScript to JavaScript (CommonJS) for the daemon.
 */

const fs = require("fs");
const fsp = require("fs/promises");
const path = require("path");
const os = require("os");
const crypto = require("crypto");
const { spawn } = require("child_process");

// ---------------------------------------------------------------------------
// 1. Persistent session directory (replaces /tmp/wa-session ephemeral)
//    Adapted from OpenClaw accounts.ts + creds-persistence.ts
// ---------------------------------------------------------------------------

/**
 * Resolve a persistent session directory for the WhatsApp account.
 *
 * Priority:
 *   1. WHATSAPP_SESSION_DIR env var (explicit operator override)
 *   2. <DATA_DIR>/whatsapp/<accountId>/ (persistent across deploys)
 *   3. <os.homedir()/.local/share/albra/whatsapp/<accountId>/ (dev fallback)
 *
 * Never uses /tmp — that directory is wiped on every Render deploy, which
 * forces a QR rescan on every restart. This is the root cause of the
 * "WhatsApp asks for QR on every restart" bug.
 */
function resolveSessionDir(accountId = "default") {
  if (process.env.WHATSAPP_SESSION_DIR) {
    return path.resolve(process.env.WHATSAPP_SESSION_DIR);
  }
  const dataDir = process.env.DATA_DIR
    || process.env.RENDER_DATA_DIR
    || path.join(os.homedir(), ".local", "share", "albra");
  return path.join(dataDir, "whatsapp", accountId);
}

/**
 * Ensure the session directory exists with mode 0o700 (owner-only).
 * Adapted from OpenClaw writeWebCredsRawAtomically() dirMode: 0o700.
 */
async function ensureSessionDir(sessionDir) {
  await fsp.mkdir(sessionDir, { recursive: true, mode: 0o700 });
  // Best-effort chmod in case mkdir didn't apply the mode on a pre-existing dir.
  try { await fsp.chmod(sessionDir, 0o700); } catch {}
}

// ---------------------------------------------------------------------------
// 2. Atomic credential writes with .bak backup + 0o600 file mode
//    Adapted from OpenClaw creds-persistence.ts:
//      writeCredsJsonAtomically()
//      writeWebCredsRawAtomically()  →  replaceFileAtomic()
//      safeSaveCreds()               →  keeps last good backup
//      restoreCredsFromBackupIfNeeded()
// ---------------------------------------------------------------------------

const CREDS_FILE_MODE = 0o600;
const CREDS_FILE_NAME = "creds.json";
const CREDS_BACKUP_NAME = "creds.json.bak";

/**
 * Write a file atomically using a temp file + rename pattern.
 * Adapted from OpenClaw replaceFileAtomic().
 *
 * - Writes to a temp file (tempPrefix + random suffix) in the same directory
 * - fsync() the temp file before rename (durability on crash)
 * - fsync() the parent directory after rename (durability of the rename)
 * - File mode is 0o600 (owner-only); dir mode is 0o700
 * - On write failure, the previous good version is preserved (no clobber)
 */
async function writeCredsAtomically(filePath, content) {
  const dir = path.dirname(filePath);
  await fsp.mkdir(dir, { recursive: true, mode: 0o700 });
  try { await fsp.chmod(dir, 0o700); } catch {}

  const tmpPath = path.join(dir, `.creds-${crypto.randomBytes(6).toString("hex")}.tmp`);
  const fh = await fsp.open(tmpPath, "w", CREDS_FILE_MODE);
  try {
    await fh.writeFile(content, { encoding: "utf8" });
    await fh.sync(); // fsync the temp file
  } finally {
    await fh.close();
  }

  try {
    // Backup current file before overwriting (if it exists and looks valid)
    try {
      const currentStat = await fsp.stat(filePath);
      if (currentStat.isFile()) {
        // Copy current → .bak (preserves last known good state)
        await fsp.copyFile(filePath, path.join(dir, CREDS_BACKUP_NAME));
      }
    } catch {
      // No existing file — that's fine, just no backup to make
    }

    // Atomic rename: temp → final
    await fsp.rename(tmpPath, filePath);

    // fsync the parent directory so the rename is durable
    try {
      const dirHandle = await fsp.open(dir, "r");
      try { await dirHandle.sync(); } finally { await dirHandle.close(); }
    } catch {
      // fsync of directories is not available on all platforms (Windows)
    }
  } catch (err) {
    // Clean up the temp file if rename failed
    try { await fsp.unlink(tmpPath); } catch {}
    throw err;
  }
}

/**
 * Restore creds.json from creds.json.bak if the main file is corrupt.
 * Adapted from OpenClaw restoreCredsFromBackupIfNeeded().
 *
 * Returns true if a backup was restored, false otherwise.
 */
async function restoreCredsFromBackupIfNeeded(sessionDir) {
  const credsPath = path.join(sessionDir, CREDS_FILE_NAME);
  const backupPath = path.join(sessionDir, CREDS_BACKUP_NAME);

  // Check if main creds file is missing or corrupt
  let mainIsCorrupt = false;
  try {
    const content = await fsp.readFile(credsPath, "utf8");
    JSON.parse(content); // throws if corrupt
  } catch (err) {
    if (err.code === "ENOENT") return false; // No main file, no backup to restore
    mainIsCorrupt = true;
  }

  if (!mainIsCorrupt) return false;

  // Try to restore from backup
  try {
    const backupContent = await fsp.readFile(backupPath, "utf8");
    JSON.parse(backupContent); // validate backup is good
    // Backup is good — restore it as the main file
    await fsp.copyFile(backupPath, credsPath);
    await fsp.chmod(credsPath, CREDS_FILE_MODE);
    return true;
  } catch {
    return false; // No backup or backup is also corrupt
  }
}

// ---------------------------------------------------------------------------
// 3. Reconnect policy with exponential backoff + jitter + maxAttempts
//    Adapted from OpenClaw reconnect.ts:
//      DEFAULT_RECONNECT_POLICY = { initialMs: 2s, maxMs: 30s, factor: 1.8,
//                                    jitter: 0.25, maxAttempts: 12 }
//      computeBackoff()
// ---------------------------------------------------------------------------

const DEFAULT_RECONNECT_POLICY = {
  initialMs: 2000,
  maxMs: 30000,
  factor: 1.8,
  jitter: 0.25,
  maxAttempts: 12,
};

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function resolveReconnectPolicy(overrides = {}) {
  const merged = { ...DEFAULT_RECONNECT_POLICY, ...overrides };
  merged.initialMs = Math.max(250, merged.initialMs);
  merged.maxMs = Math.max(merged.initialMs, merged.maxMs);
  merged.factor = clamp(merged.factor, 1.1, 10);
  merged.jitter = clamp(merged.jitter, 0, 1);
  merged.maxAttempts = Math.max(0, Math.floor(merged.maxAttempts));
  return merged;
}

/**
 * Compute backoff delay for a given attempt number using exponential
 * backoff with jitter.
 *
 * Adapted from OpenClaw computeBackoff():
 *   delay = min(maxMs, initialMs * factor^(attempt-1))
 *   jitter_amount = delay * jitter
 *   final = delay - random(0, jitter_amount)
 *
 * The jitter is subtractive (not additive) so the delay never exceeds maxMs.
 */
function computeBackoff(policy, attempt) {
  const base = Math.min(
    policy.maxMs,
    policy.initialMs * Math.pow(policy.factor, Math.max(0, attempt - 1))
  );
  const jitterAmount = base * policy.jitter;
  return Math.max(policy.initialMs, base - Math.random() * jitterAmount);
}

// ---------------------------------------------------------------------------
// 4. Dual watchdog timers (transport-level WS frames + app-level inbound msgs)
//    Adapted from OpenClaw connection-controller.ts:
//      startTimers()
//      lastTransportActivityAt  ←  sock.ws.on("frame")
//      lastInboundAt            ←  message handler
//      appSilenceTimeoutMs = messageTimeoutMs * 4
//      openedAfterRecentInbound
//      WHATSAPP_WATCHDOG_TIMEOUT_ERROR
// ---------------------------------------------------------------------------

const WHATSAPP_WATCHDOG_TIMEOUT_ERROR = "watchdog-timeout";
const DEFAULT_TRANSPORT_TIMEOUT_MS = 5 * 60 * 1000;  // 5 min with no WS frames = dead socket
const DEFAULT_MESSAGE_TIMEOUT_MS = 30 * 60 * 1000;  // 30 min with no inbound = stale session
const DEFAULT_WATCHDOG_CHECK_MS = 30 * 1000;          // check every 30s

/**
 * Create a dual watchdog for a WhatsApp socket.
 *
 * - transportActivity: socket is alive at the WS frame level
 * - appActivity: messages are actually being received and processed
 *
 * If either signal goes stale, the watchdog fires `onTimeout` so the
 * caller can force-close the socket and reconnect.
 *
 * Adapted from OpenClaw connection-controller.ts startTimers():
 *   transportStaleForMs = now - lastTransportActivityAt
 *   appSilentForMs = now - (lastInboundAt ?? startedAt)
 *   appSilenceTimeoutMs = openedAfterRecentInbound ? messageTimeoutMs : messageTimeoutMs*4
 *   if (transportStaleForMs > transportTimeoutMs || appSilentForMs > appSilenceTimeoutMs)
 *     → forceClose({status: 499, error: WHATSAPP_WATCHDOG_TIMEOUT_ERROR})
 */
function createWatchdog(sock, options = {}) {
  const transportTimeoutMs = options.transportTimeoutMs || DEFAULT_TRANSPORT_TIMEOUT_MS;
  const messageTimeoutMs = options.messageTimeoutMs || DEFAULT_MESSAGE_TIMEOUT_MS;
  const checkIntervalMs = options.checkIntervalMs || DEFAULT_WATCHDOG_CHECK_MS;
  const openedAfterRecentInbound = Boolean(options.openedAfterRecentInbound);

  // 4× grace window right after a reconnect that followed recent messages
  // (so a quiet period isn't misjudged as broken)
  const appSilenceTimeoutMs = openedAfterRecentInbound ? messageTimeoutMs : messageTimeoutMs * 4;

  const state = {
    startedAt: Date.now(),
    lastTransportActivityAt: Date.now(),
    lastInboundAt: null,
    handledMessages: 0,
    timer: null,
    stopped: false,
  };

  // Attach WS frame listener (transport-level heartbeat)
  let unregisterTransportActivity = null;
  try {
    if (sock?.ws?.on) {
      const noteActivity = () => { state.lastTransportActivityAt = Date.now(); };
      sock.ws.on("frame", noteActivity);
      // Also listen to any incoming message event as transport activity
      sock.ws.on("message", noteActivity);
      unregisterTransportActivity = () => {
        try { sock.ws.off("frame", noteActivity); } catch {}
        try { sock.ws.off("message", noteActivity); } catch {}
      };
    }
  } catch {
    // Best-effort: if we can't attach to ws, transport watchdog is disabled
  }

  function noteInbound() {
    state.handledMessages += 1;
    state.lastInboundAt = Date.now();
    state.lastTransportActivityAt = Date.now();
  }

  function tick() {
    if (state.stopped) return;
    const now = Date.now();
    const transportStaleForMs = now - state.lastTransportActivityAt;
    const appSilentForMs = now - (state.lastInboundAt ?? state.startedAt);

    if (transportStaleForMs > transportTimeoutMs) {
      options.onTimeout?.({
        reason: "transport_stale",
        transportStaleForMs,
        appSilentForMs,
        handledMessages: state.handledMessages,
      });
      stop();
      return;
    }
    if (appSilentForMs > appSilenceTimeoutMs) {
      options.onTimeout?.({
        reason: "app_silent",
        transportStaleForMs,
        appSilentForMs,
        handledMessages: state.handledMessages,
      });
      stop();
      return;
    }
  }

  state.timer = setInterval(tick, checkIntervalMs);
  // Don't keep the event loop alive just for the watchdog
  if (state.timer.unref) state.timer.unref();

  function stop() {
    state.stopped = true;
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
    unregisterTransportActivity?.();
    unregisterTransportActivity = null;
  }

  function snapshot() {
    return {
      startedAt: state.startedAt,
      lastTransportActivityAt: state.lastTransportActivityAt,
      lastInboundAt: state.lastInboundAt,
      handledMessages: state.handledMessages,
      uptimeMs: Date.now() - state.startedAt,
    };
  }

  return { noteInbound, stop, snapshot };
}

// ---------------------------------------------------------------------------
// 5. Cross-process file lock (Linux flock / Windows lockfile)
//    Adapted from OpenClaw connection-owner.ts:
//      acquireWhatsAppGatewayConnectionOwner()
//      OWNER_LOCK_STALE_MS = 5 * 60_000
//      WhatsAppConnectionOwnerBusyError
// ---------------------------------------------------------------------------

const OWNER_LOCK_STALE_MS = 5 * 60 * 1000; // 5 minutes

class WhatsAppConnectionOwnerBusyError extends Error {
  constructor(authDir) {
    super(`Another process owns the WhatsApp connection at ${authDir}`);
    this.name = "WhatsAppConnectionOwnerBusyError";
    this.code = "whatsapp_connection_owner_busy";
    this.authDir = authDir;
  }
}

/**
 * Acquire an exclusive file lock on the session directory.
 *
 * On Linux: uses flock(2) via a child process. The lock is released when
 * the process exits or when release() is called.
 *
 * On Windows: uses a lockfile with PID + timestamp. Stale if the PID is
 * no longer alive or older than OWNER_LOCK_STALE_MS.
 *
 * Adapted from OpenClaw connection-owner.ts acquireOwnerLease().
 *
 * Returns { release } on success.
 * Throws WhatsAppConnectionOwnerBusyError if another process holds the lock.
 */
async function acquireConnectionOwnerLock(authDir) {
  await fsp.mkdir(authDir, { recursive: true, mode: 0o700 });
  const lockPath = path.join(authDir, ".owner.lock");

  // Check for existing lock and stale recovery
  try {
    const existing = await fsp.readFile(lockPath, "utf8");
    const parsed = JSON.parse(existing);
    const ageMs = Date.now() - (parsed.timestamp || 0);
    const isStale = ageMs > OWNER_LOCK_STALE_MS;

    if (!isStale && parsed.pid) {
      // Check if PID is alive
      const isAlive = await isPidAlive(parsed.pid);
      if (isAlive) {
        throw new WhatsAppConnectionOwnerBusyError(authDir);
      }
      // PID is dead — stale lock, can take over
    }
  } catch (err) {
    if (err instanceof WhatsAppConnectionOwnerBusyError) throw err;
    // ENOENT = no existing lock, that's fine
    // JSON parse error = corrupt lockfile, treat as no lock
  }

  // Write our lock
  const lockContent = JSON.stringify({
    pid: process.pid,
    timestamp: Date.now(),
    hostname: os.hostname(),
  });
  await fsp.writeFile(lockPath, lockContent, { encoding: "utf8", mode: 0o600 });

  return {
    release: async () => {
      try {
        // Only delete if it's still our lock
        const current = await fsp.readFile(lockPath, "utf8");
        const parsed = JSON.parse(current);
        if (parsed.pid === process.pid) {
          await fsp.unlink(lockPath);
        }
      } catch {
        // Best-effort cleanup
      }
    },
  };
}

/**
 * Check if a process with the given PID is alive.
 * Cross-platform: uses `kill -0` on Unix, `tasklist` on Windows.
 */
async function isPidAlive(pid) {
  return new Promise((resolve) => {
    if (process.platform === "win32") {
      spawn("tasklist", ["/FI", `PID eq ${pid}`], { stdio: ["ignore", "pipe", "ignore"] })
        .on("exit", (code) => resolve(code === 0))
        .on("error", () => resolve(false));
    } else {
      try {
        process.kill(pid, 0);
        resolve(true);
      } catch {
        resolve(false);
      }
    }
  });
}

// ---------------------------------------------------------------------------
// 6. Persistent message deduplication (sha256(jid+msgId), 7-day TTL, 5000 entries)
//    Adapted from OpenClaw durable-receive.ts:
//      createWhatsAppDurableInboundMessageId({remoteJid, id})
//      retention: { completedTtlMs: 7 * 24 * 60 * 60 * 1000, completedMaxEntries: 5000 }
// ---------------------------------------------------------------------------

const DEDUP_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days
const DEDUP_MAX_ENTRIES = 5000;

/**
 * Create a durable message ID from remoteJid + id using sha256.
 *
 * Adapted from OpenClaw createWhatsAppDurableInboundMessageId():
 *   createHash("sha256").update(`${remoteJid}\n${id}`).digest("hex")
 *
 * The hash is used as the dedup key (not the raw msgId) because:
 *   - msgId alone can collide across different chats
 *   - remoteJid + msgId together are unique per message
 *   - hashing keeps the key fixed-length (64 hex chars) regardless of input size
 */
function createDurableMessageId(remoteJid, id) {
  return crypto.createHash("sha256")
    .update(`${remoteJid}\n${id}`)
    .digest("hex");
}

/**
 * Persistent dedup store backed by a simple JSON file.
 *
 * Adapted from OpenClaw createPersistentDedupeCache() — but using a flat
 * JSON file instead of a keyed store plugin. Auto-disables on first failure
 * so message handling never breaks (same fail-open behavior as OpenClaw).
 *
 * Layout: { entries: { hashKey: timestamp }, lastPrunedAt: timestamp }
 * Storage: <sessionDir>/dedup.json (mode 0o600)
 */
function createPersistentDedupStore(sessionDir) {
  const dedupPath = path.join(sessionDir, "dedup.json");
  let cache = null;
  let disabled = false;
  let saveTimer = null;

  async function load() {
    if (disabled) return;
    if (cache) return;
    try {
      const content = await fsp.readFile(dedupPath, "utf8");
      cache = JSON.parse(content);
      if (!cache || typeof cache !== "object" || !cache.entries) {
        cache = { entries: {}, lastPrunedAt: Date.now() };
      }
    } catch (err) {
      if (err.code === "ENOENT") {
        cache = { entries: {}, lastPrunedAt: Date.now() };
      } else {
        // Corrupt or unreadable — disable persistent dedup, fall back to in-memory only
        disabled = true;
        cache = { entries: {}, lastPrunedAt: Date.now() };
      }
    }
  }

  function scheduleSave() {
    if (disabled) return;
    if (saveTimer) return;
    saveTimer = setTimeout(async () => {
      saveTimer = null;
      await flush();
    }, 1000);
    if (saveTimer.unref) saveTimer.unref();
  }

  async function flush() {
    if (disabled || !cache) return;
    try {
      await fsp.mkdir(path.dirname(dedupPath), { recursive: true, mode: 0o700 });
      const tmpPath = dedupPath + ".tmp." + crypto.randomBytes(4).toString("hex");
      await fsp.writeFile(tmpPath, JSON.stringify(cache), { encoding: "utf8", mode: 0o600 });
      await fsp.rename(tmpPath, dedupPath);
    } catch {
      // Best-effort — disable if write fails repeatedly
    }
  }

  function prune() {
    if (!cache) return;
    const now = Date.now();
    const entries = cache.entries;
    const keys = Object.keys(entries);

    // TTL prune
    for (const key of keys) {
      if (now - entries[key] > DEDUP_TTL_MS) {
        delete entries[key];
      }
    }

    // Size cap (LRU-ish: just delete oldest entries beyond cap)
    const remaining = Object.keys(entries);
    if (remaining.length > DEDUP_MAX_ENTRIES) {
      const sorted = remaining.sort((a, b) => entries[a] - entries[b]);
      const toDelete = sorted.slice(0, remaining.length - DEDUP_MAX_ENTRIES);
      for (const key of toDelete) delete entries[key];
    }

    cache.lastPrunedAt = now;
  }

  return {
    /**
     * Returns true if the message was already processed (duplicate).
     * Marks it as processed otherwise.
     */
    async isDuplicate(remoteJid, msgId) {
      if (!remoteJid || !msgId) return false;
      await load();
      if (disabled) return false;

      const key = createDurableMessageId(remoteJid, msgId);
      const now = Date.now();

      // Periodic prune
      if (now - (cache.lastPrunedAt || 0) > 5 * 60 * 1000) {
        prune();
      }

      if (cache.entries[key]) {
        // Check TTL just in case
        if (now - cache.entries[key] < DEDUP_TTL_MS) {
          return true; // duplicate
        }
      }

      cache.entries[key] = now;
      scheduleSave();
      return false;
    },

    async flush() {
      return flush();
    },

    isDisabled() {
      return disabled;
    },
  };
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

module.exports = {
  // Session persistence
  resolveSessionDir,
  ensureSessionDir,
  // Atomic credential writes
  writeCredsAtomically,
  restoreCredsFromBackupIfNeeded,
  // Reconnect policy
  DEFAULT_RECONNECT_POLICY,
  resolveReconnectPolicy,
  computeBackoff,
  // Watchdog
  createWatchdog,
  WHATSAPP_WATCHDOG_TIMEOUT_ERROR,
  DEFAULT_TRANSPORT_TIMEOUT_MS,
  DEFAULT_MESSAGE_TIMEOUT_MS,
  // File lock
  acquireConnectionOwnerLock,
  WhatsAppConnectionOwnerBusyError,
  OWNER_LOCK_STALE_MS,
  // Dedup
  createDurableMessageId,
  createPersistentDedupStore,
  DEDUP_TTL_MS,
  DEDUP_MAX_ENTRIES,
};
