// Test smoke for openclaw-adapted.js utilities
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const os = require("os");
const {
  resolveSessionDir,
  ensureSessionDir,
  writeCredsAtomically,
  restoreCredsFromBackupIfNeeded,
  resolveReconnectPolicy,
  computeBackoff,
  DEFAULT_RECONNECT_POLICY,
  createWatchdog,
  WHATSAPP_WATCHDOG_TIMEOUT_ERROR,
  acquireConnectionOwnerLock,
  WhatsAppConnectionOwnerBusyError,
  createDurableMessageId,
  createPersistentDedupStore,
  DEDUP_TTL_MS,
  DEDUP_MAX_ENTRIES,
} = require("../src/server/openclaw-adapted");

let passed = 0;
let failed = 0;
function ok(name, cond) {
  if (cond) { passed++; console.log(`  ✅ ${name}`); }
  else { failed++; console.log(`  ❌ ${name}`); }
}

// === 1. Session dir resolution ===
console.log("\n=== Session dir resolution ===");
const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "wa-test-"));
process.env.DATA_DIR = tmpRoot;
const sessionDir = resolveSessionDir("test-account");
ok("sessionDir is under DATA_DIR/whatsapp/accountId", sessionDir === path.join(tmpRoot, "whatsapp", "test-account"));
ok("sessionDir is NOT in /tmp/wa-session", !sessionDir.includes("/tmp/wa-session"));

// === 2. ensureSessionDir creates with mode 0o700 ===
console.log("\n=== ensureSessionDir ===");
(async () => {
  await ensureSessionDir(sessionDir);
  const stat = fs.statSync(sessionDir);
  const mode = stat.mode & 0o777;
  ok("session dir created", stat.isDirectory());
  // On Linux, mode should be 0o700 (some systems may not enforce — soft check)
  ok("session dir mode is 0o700", mode === 0o700);

  // === 3. Atomic credential writes ===
  console.log("\n=== Atomic credential writes ===");
  const credsPath = path.join(sessionDir, "creds.json");
  const creds1 = { user: "alice", tokens: ["a", "b"] };
  await writeCredsAtomically(credsPath, JSON.stringify(creds1));
  const read1 = JSON.parse(fs.readFileSync(credsPath, "utf8"));
  ok("writeCredsAtomically writes content", read1.user === "alice");

  // Second write should create .bak with previous content
  const creds2 = { user: "bob", tokens: ["c"] };
  await writeCredsAtomically(credsPath, JSON.stringify(creds2));
  const read2 = JSON.parse(fs.readFileSync(credsPath, "utf8"));
  ok("second write replaces content", read2.user === "bob");
  const backupExists = fs.existsSync(path.join(sessionDir, "creds.json.bak"));
  ok("backup .bak created", backupExists);
  const backupContent = JSON.parse(fs.readFileSync(path.join(sessionDir, "creds.json.bak"), "utf8"));
  ok("backup contains previous content", backupContent.user === "alice");

  // === 4. restoreCredsFromBackupIfNeeded ===
  console.log("\n=== restoreCredsFromBackupIfNeeded ===");
  // Corrupt the main creds.json
  fs.writeFileSync(credsPath, "{corrupt json", { mode: 0o600 });
  const restored = await restoreCredsFromBackupIfNeeded(sessionDir);
  ok("restored from backup when main is corrupt", restored === true);
  const restoredContent = JSON.parse(fs.readFileSync(credsPath, "utf8"));
  ok("restored content is the backup (alice)", restoredContent.user === "alice");

  // When main is fine, returns false
  const noRestore = await restoreCredsFromBackupIfNeeded(sessionDir);
  ok("no restore when main is valid", noRestore === false);

  // === 5. Reconnect policy ===
  console.log("\n=== Reconnect policy ===");
  const policy = resolveReconnectPolicy();
  ok("default initialMs = 2000", policy.initialMs === 2000);
  ok("default maxMs = 30000", policy.maxMs === 30000);
  ok("default factor = 1.8", policy.factor === 1.8);
  ok("default jitter = 0.25", policy.jitter === 0.25);
  ok("default maxAttempts = 12", policy.maxAttempts === 12);

  // Backoff computation
  const b1 = computeBackoff(policy, 1);
  ok("backoff attempt 1 is between initialMs and maxMs", b1 >= 1500 && b1 <= 2000);
  const b5 = computeBackoff(policy, 5);
  ok("backoff attempt 5 grows but stays under maxMs", b5 > b1 && b5 <= policy.maxMs);
  const b20 = computeBackoff(policy, 20);
  ok("backoff caps at maxMs for large attempts", b20 <= policy.maxMs);

  // === 6. Watchdog ===
  console.log("\n=== Watchdog ===");
  await new Promise((resolve) => {
    let timeoutCalled = false;
    const fakeSock = {
      ws: {
        on: (event, cb) => {},
        off: (event, cb) => {},
      },
    };
    const wd = createWatchdog(fakeSock, {
      transportTimeoutMs: 50, // very short for testing
      messageTimeoutMs: 50,
      checkIntervalMs: 10,
      onTimeout: (info) => {
        timeoutCalled = true;
        ok("watchdog fires onTimeout when transport is stale", info.reason === "transport_stale");
        wd.stop();
        resolve();
      },
    });
    // Don't call noteInbound — should time out
    setTimeout(() => {
      if (!timeoutCalled) {
        ok("watchdog fires onTimeout when transport is stale", false);
        wd.stop();
        resolve();
      }
    }, 200);
  });

  // Watchdog with inbound activity — should NOT fire
  await new Promise((resolve) => {
    let timeoutCalled = false;
    const fakeSock = { ws: { on: () => {}, off: () => {} } };
    const wd = createWatchdog(fakeSock, {
      transportTimeoutMs: 100,
      messageTimeoutMs: 100,
      checkIntervalMs: 20,
      onTimeout: () => {
        timeoutCalled = true;
      },
    });
    // Simulate inbound messages
    const interval = setInterval(() => wd.noteInbound(), 30);
    setTimeout(() => {
      clearInterval(interval);
      ok("watchdog does NOT fire when inbound activity is healthy", !timeoutCalled);
      wd.stop();
      resolve();
    }, 200);
  });

  // === 7. File lock ===
  console.log("\n=== File lock ===");
  const lockDir = path.join(tmpRoot, "lock-test");
  const lease1 = await acquireConnectionOwnerLock(lockDir);
  ok("first acquire succeeds", !!lease1);

  // Second acquire from same process should fail (we hold the lock)
  try {
    await acquireConnectionOwnerLock(lockDir);
    ok("second acquire throws WhatsAppConnectionOwnerBusyError", false);
  } catch (err) {
    ok("second acquire throws WhatsAppConnectionOwnerBusyError",
      err instanceof WhatsAppConnectionOwnerBusyError);
  }

  // Release and acquire again
  await lease1.release();
  const lease2 = await acquireConnectionOwnerLock(lockDir);
  ok("acquire after release succeeds", !!lease2);
  await lease2.release();

  // === 8. Durable dedup ===
  console.log("\n=== Durable dedup ===");
  const dedupDir = path.join(tmpRoot, "dedup-test");
  await ensureSessionDir(dedupDir);
  const dedup = createPersistentDedupStore(dedupDir);

  const jid = "573001112223@s.whatsapp.net";
  const msgId = "3EB0E1A1B2C3";

  // First call: not duplicate
  const isFirstDup = await dedup.isDuplicate(jid, msgId);
  ok("first call is not duplicate", isFirstDup === false);

  // Second call: duplicate
  const isSecondDup = await dedup.isDuplicate(jid, msgId);
  ok("second call with same jid+msgId is duplicate", isSecondDup === true);

  // Different msgId: not duplicate
  const isOtherDup = await dedup.isDuplicate(jid, "DIFFERENT_MSG_ID");
  ok("different msgId is not duplicate", isOtherDup === false);

  // Different jid, same msgId: not duplicate (sha256 includes jid)
  const isOtherJidDup = await dedup.isDuplicate("other@s.whatsapp.net", msgId);
  ok("different jid + same msgId is not duplicate", isOtherJidDup === false);

  // Flush persists to disk
  await dedup.flush();
  const dedupFile = path.join(dedupDir, "dedup.json");
  ok("dedup.json was persisted", fs.existsSync(dedupFile));
  const persisted = JSON.parse(fs.readFileSync(dedupFile, "utf8"));
  ok("persisted dedup has the entry", Object.keys(persisted.entries).length > 0);

  // Reload dedup from disk — should still know about the duplicate
  const dedup2 = createPersistentDedupStore(dedupDir);
  const isStillDup = await dedup2.isDuplicate(jid, msgId);
  ok("dedup survives restart (still duplicate)", isStillDup === true);

  // sha256 key is 64 hex chars
  const key = createDurableMessageId(jid, msgId);
  ok("createDurableMessageId returns 64-char sha256 hex", /^[0-9a-f]{64}$/.test(key));

  // TTL constants match OpenClaw
  ok("DEDUP_TTL_MS = 7 days", DEDUP_TTL_MS === 7 * 24 * 60 * 60 * 1000);
  ok("DEDUP_MAX_ENTRIES = 5000", DEDUP_MAX_ENTRIES === 5000);

  // Cleanup
  try { fs.rmSync(tmpRoot, { recursive: true, force: true }); } catch {}

  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})().catch((err) => {
  console.error("Test crashed:", err);
  process.exit(1);
});
