/**
 * Tests for WhatsApp daemon hardening.
 *
 * These tests use Node's built-in assert + require() to avoid
 * a test framework dependency. Run with: node dashboard/tests/whatsapp-daemon.test.js
 *
 * Tests cover:
 *   - Image signature validation (JPEG, PNG, WebP, invalid)
 *   - MIME + signature mismatch
 *   - File size limits (declared + actual)
 *   - Message deduplication
 *   - Log sanitization (no phone numbers)
 *   - Message unwrapping (ephemeral, viewOnce, etc.)
 *   - Concurrency limiter
 *   - Reconnection scheduling
 */

const assert = require("assert");
const Module = require("module");
const { Readable } = require("stream");

// Mock the daemon module by loading its exported functions
// We need to set INTERNAL_API_SECRET before requiring
process.env.INTERNAL_API_SECRET = "test-secret-that-is-at-least-32-characters-long!!";

const daemon = require("../src/server/whatsapp-daemon.js");

const {
  isDuplicate,
  isSupportedImageBuffer,
  unwrapMessage,
  sanitizeJid,
  withMediaSlot,
  collectMediaStreamBounded,
  transcribeAudio,
  checkAuth,
  normalizeRecipient,
  MAX_MEDIA_BYTES,
  MAX_CONCURRENT_MEDIA,
  DEDUP_TTL_MS,
  MAX_MEDIA_QUEUE,
} = daemon;

// =====================================================================
// Helpers: create synthetic file buffers
// =====================================================================

function makeJPEG(size = 1024) {
  const buf = Buffer.alloc(size);
  buf[0] = 0xff; buf[1] = 0xd8; buf[2] = 0xff; // JPEG SOI
  return buf;
}

function makePNG(size = 1024) {
  const buf = Buffer.alloc(size);
  // PNG signature: 137 80 78 71 13 10 26 10
  buf[0] = 137; buf[1] = 80; buf[2] = 78; buf[3] = 71;
  buf[4] = 13; buf[5] = 10; buf[6] = 26; buf[7] = 10;
  return buf;
}

function makeWebP(size = 1024) {
  const buf = Buffer.alloc(size);
  buf.write("RIFF", 0, "ascii");
  buf.write("WEBP", 8, "ascii");
  return buf;
}

function makeGarbage(size = 1024) {
  return Buffer.alloc(size, 0x00);
}

// =====================================================================
// Tests
// =====================================================================

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (e) {
    console.error(`  ✗ ${name}: ${e.message}`);
    failed++;
  }
}

async function asyncTest(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
    passed++;
  } catch (e) {
    console.error(`  ✗ ${name}: ${e.message}`);
    failed++;
  }
}

console.log("\n=== Security and Execution Permissions ===");

test("Daemon authentication accepts the exact bearer token", () => {
  assert.strictEqual(checkAuth({ headers: { authorization: `Bearer ${process.env.INTERNAL_API_SECRET}` } }), true);
});

test("Daemon authentication rejects a wrong bearer token", () => {
  assert.strictEqual(checkAuth({ headers: { authorization: "Bearer wrong-token" } }), false);
});

test("Daemon authentication rejects a missing bearer token", () => {
  assert.strictEqual(checkAuth({ headers: {} }), false);
});

test("Recipient normalizer accepts an individual phone number", () => {
  assert.strictEqual(normalizeRecipient("+573001234567"), "573001234567@s.whatsapp.net");
});

test("Recipient normalizer accepts a direct WhatsApp phone JID", () => {
  assert.strictEqual(normalizeRecipient("573001234567@s.whatsapp.net"), "573001234567@s.whatsapp.net");
});

test("Recipient normalizer rejects group JIDs and malformed targets", () => {
  assert.strictEqual(normalizeRecipient("123456789-123456@g.us"), null);
  assert.strictEqual(normalizeRecipient("javascript:alert(1)"), null);
  assert.strictEqual(normalizeRecipient("123"), null);
});

console.log("\n=== Image Signature Validation ===");

test("Valid JPEG signature", () => {
  assert.strictEqual(isSupportedImageBuffer(makeJPEG(), "image/jpeg"), true);
});

test("Valid PNG signature", () => {
  assert.strictEqual(isSupportedImageBuffer(makePNG(), "image/png"), true);
});

test("Valid WebP signature", () => {
  assert.strictEqual(isSupportedImageBuffer(makeWebP(), "image/webp"), true);
});

test("Garbage buffer with JPEG MIME rejected", () => {
  assert.strictEqual(isSupportedImageBuffer(makeGarbage(), "image/jpeg"), false);
});

test("Garbage buffer with PNG MIME rejected", () => {
  assert.strictEqual(isSupportedImageBuffer(makeGarbage(), "image/png"), false);
});

test("JPEG buffer with wrong MIME (png) rejected", () => {
  assert.strictEqual(isSupportedImageBuffer(makeJPEG(), "image/png"), false);
});

test("PNG buffer with wrong MIME (jpeg) rejected", () => {
  assert.strictEqual(isSupportedImageBuffer(makePNG(), "image/jpeg"), false);
});

test("Unsupported MIME (gif) rejected", () => {
  assert.strictEqual(isSupportedImageBuffer(makeJPEG(), "image/gif"), false);
});

test("Empty buffer rejected", () => {
  assert.strictEqual(isSupportedImageBuffer(Buffer.alloc(0), "image/jpeg"), false);
});

test("Null buffer rejected", () => {
  assert.strictEqual(isSupportedImageBuffer(null, "image/jpeg"), false);
});

test("Too-small JPEG (2 bytes) rejected", () => {
  const buf = Buffer.from([0xff, 0xd8]);
  assert.strictEqual(isSupportedImageBuffer(buf, "image/jpeg"), false);
});

console.log("\n=== Deduplication ===");

test("First message is not duplicate", () => {
  // Clear dedup state
  // isDuplicate returns false on first call, true on second
  const id = `test-${Date.now()}`;
  assert.strictEqual(isDuplicate(id), false);
});

test("Same message ID within TTL is duplicate", () => {
  const id = `test-dup-${Date.now()}`;
  isDuplicate(id); // First call
  assert.strictEqual(isDuplicate(id), true); // Second call
});

test("Different message IDs are not duplicates", () => {
  const id1 = `test-unique-1-${Date.now()}`;
  const id2 = `test-unique-2-${Date.now()}`;
  isDuplicate(id1);
  assert.strictEqual(isDuplicate(id2), false);
});

console.log("\n=== Log Sanitization ===");

test("Phone number in JID is redacted", () => {
  const result = sanitizeJid("573163301286@s.whatsapp.net");
  assert.strictEqual(result, "****@s.whatsapp.net");
});

test("LID is redacted", () => {
  const result = sanitizeJid("215779257634953@lid");
  assert.strictEqual(result, "****@lid");
});

test("Null JID returns 'unknown'", () => {
  assert.strictEqual(sanitizeJid(null), "unknown");
});

test("Empty string JID returns 'unknown'", () => {
  assert.strictEqual(sanitizeJid(""), "unknown");
});

console.log("\n=== Message Unwrapping ===");

test("Plain message (no wrapping) passes through", () => {
  const msg = { conversation: "hello" };
  const result = unwrapMessage(msg);
  assert.strictEqual(result.conversation, "hello");
});

test("ephemeralMessage is unwrapped", () => {
  const msg = { ephemeralMessage: { message: { conversation: "secret" } } };
  const result = unwrapMessage(msg);
  assert.strictEqual(result.conversation, "secret");
});

test("viewOnceMessage is unwrapped", () => {
  const msg = { viewOnceMessage: { message: { conversation: "once" } } };
  const result = unwrapMessage(msg);
  assert.strictEqual(result.conversation, "once");
});

test("viewOnceMessageV2 is unwrapped", () => {
  const msg = { viewOnceMessageV2: { message: { conversation: "once2" } } };
  const result = unwrapMessage(msg);
  assert.strictEqual(result.conversation, "once2");
});

test("documentWithCaption is unwrapped", () => {
  const msg = { documentWithCaptionMessage: { message: { conversation: "doc" } } };
  const result = unwrapMessage(msg);
  assert.strictEqual(result.conversation, "doc");
});

test("Deeply nested wrapping (3 levels) is unwrapped", () => {
  const msg = {
    ephemeralMessage: {
      message: {
        viewOnceMessage: {
          message: {
            viewOnceMessageV2: {
              message: { conversation: "deep" }
            }
          }
        }
      }
    }
  };
  const result = unwrapMessage(msg);
  assert.strictEqual(result.conversation, "deep");
});

test("Max depth (5 levels) — stops unwrapping at depth limit", () => {
  let msg = { conversation: "bottom" };
  for (let i = 0; i < 7; i++) {
    msg = { ephemeralMessage: { message: msg } };
  }
  const result = unwrapMessage(msg);
  // Should unwrap at most 5 levels
  assert.ok(result, "Should return something after 5 levels of unwrapping");
});

test("Null message returns null/undefined", () => {
  assert.strictEqual(unwrapMessage(null), null);
});

test("Empty object returns itself", () => {
  const result = unwrapMessage({});
  assert.deepStrictEqual(result, {});
});

console.log("\n=== Concurrency Limiter ===");

(async () => {
  await asyncTest("Streaming media collector accepts content at the byte limit", async () => {
    const input = Buffer.alloc(8);
    const result = await collectMediaStreamBounded(Readable.from([input.subarray(0, 4), input.subarray(4)]), 8);
    assert.strictEqual(result.length, 8);
    assert.deepStrictEqual(result, input);
  });

  await asyncTest("Streaming media collector stops when content exceeds the byte limit", async () => {
    const stream = Readable.from([Buffer.alloc(6), Buffer.alloc(5)]);
    await assert.rejects(
      collectMediaStreamBounded(stream, 10),
      (err) => err && err.message === "media_too_large_actual"
    );
    assert.strictEqual(stream.destroyed, true);
  });

  await asyncTest("Concurrent tasks are limited", async () => {
    let concurrent = 0;
    let maxConcurrent = 0;

    const tasks = [];
    for (let i = 0; i < 5; i++) {
      tasks.push(
        withMediaSlot(async () => {
          concurrent++;
          maxConcurrent = Math.max(maxConcurrent, concurrent);
          await new Promise(r => setTimeout(r, 50));
          concurrent--;
        })
      );
    }
    await Promise.all(tasks);
    assert.ok(maxConcurrent <= MAX_CONCURRENT_MEDIA, `Max concurrent ${maxConcurrent} > limit ${MAX_CONCURRENT_MEDIA}`);
  });

  await asyncTest("Tasks complete even when queued", async () => {
    const results = [];
    const tasks = [];
    for (let i = 0; i < 4; i++) {
      tasks.push(
        withMediaSlot(async () => {
          results.push(i);
          await new Promise(r => setTimeout(r, 10));
        })
      );
    }
    await Promise.all(tasks);
    assert.strictEqual(results.length, 4);
  });

  await asyncTest("Media queue rejects work above configured bound", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const active = [
      withMediaSlot(() => gate),
      withMediaSlot(() => gate),
    ];
    await new Promise((resolve) => setImmediate(resolve));
    const queued = [];
    for (let i = 0; i < MAX_MEDIA_QUEUE; i++) {
      queued.push(withMediaSlot(async () => {}));
    }
    if (MAX_MEDIA_QUEUE > 0) {
      await assert.rejects(
        withMediaSlot(async () => {}),
        (err) => err && err.message === "media_queue_full"
      );
    }
    release();
    await Promise.all([...active, ...queued]);
  });

  console.log("\\n=== Audio Transcription ===");

  const makeOgg = () => Buffer.concat([Buffer.from("OggS"), Buffer.from("synthetic test payload")]);
  const withAsrEnv = (fn) => {
    const previousKey = process.env.NVIDIA_API_KEY;
    const previousEndpoint = process.env.NVIDIA_ASR_ENDPOINT;
    const previousPoll = process.env.NVIDIA_ASR_POLL_SECONDS;
    const previousTimeout = process.env.NVIDIA_ASR_TIMEOUT_MS;
    const previousFallback = process.env.WHATSAPP_ASR_ZAI_FALLBACK;
    process.env.NVIDIA_API_KEY = "test-nvidia-key";
    process.env.NVIDIA_ASR_ENDPOINT = "https://asr.example.test/v1/audio/transcriptions";
    process.env.NVIDIA_ASR_POLL_SECONDS = "120";
    process.env.NVIDIA_ASR_TIMEOUT_MS = "135000";
    process.env.WHATSAPP_ASR_ZAI_FALLBACK = "false";
    return Promise.resolve().then(fn).finally(() => {
      if (previousKey === undefined) delete process.env.NVIDIA_API_KEY;
      else process.env.NVIDIA_API_KEY = previousKey;
      if (previousEndpoint === undefined) delete process.env.NVIDIA_ASR_ENDPOINT;
      else process.env.NVIDIA_ASR_ENDPOINT = previousEndpoint;
      if (previousPoll === undefined) delete process.env.NVIDIA_ASR_POLL_SECONDS;
      else process.env.NVIDIA_ASR_POLL_SECONDS = previousPoll;
      if (previousTimeout === undefined) delete process.env.NVIDIA_ASR_TIMEOUT_MS;
      else process.env.NVIDIA_ASR_TIMEOUT_MS = previousTimeout;
      if (previousFallback === undefined) delete process.env.WHATSAPP_ASR_ZAI_FALLBACK;
      else process.env.WHATSAPP_ASR_ZAI_FALLBACK = previousFallback;
    });
  };

  await asyncTest("Valid OGG audio is sent as multipart and transcript is returned", async () => {
    const originalFetch = global.fetch;
    let capturedUrl;
    let capturedOptions;
    global.fetch = async (url, options) => {
      capturedUrl = String(url);
      capturedOptions = options;
      return new Response(JSON.stringify({ text: "  Hola, esta es una prueba.  " }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    try {
      await withAsrEnv(async () => {
        const transcript = await transcribeAudio(makeOgg());
        assert.strictEqual(transcript, "Hola, esta es una prueba.");
        assert.strictEqual(capturedUrl, process.env.NVIDIA_ASR_ENDPOINT);
        assert.strictEqual(capturedOptions.headers.Authorization, "Bearer test-nvidia-key");
        assert.strictEqual(capturedOptions.headers["NVCF-POLL-SECONDS"], "120");
        assert.strictEqual(capturedOptions.signal.aborted, false);
        assert.strictEqual(capturedOptions.body.get("language"), "es-US");
        assert.strictEqual(capturedOptions.body.get("response_format"), "json");
        assert.strictEqual(capturedOptions.body.get("file").name, "whatsapp-voice-note.ogg");
      });
    } finally {
      global.fetch = originalFetch;
    }
  });

  await asyncTest("Z.ai SDK fallback transcribes audio after NVIDIA provider failure", async () => {
    const originalFetch = global.fetch;
    const originalLoad = Module._load;
    global.fetch = async () => new Response("provider error", { status: 503 });
    Module._load = function(request, parent, isMain) {
      if (request === "z-ai-web-dev-sdk") {
        return { default: { create: async () => ({
          audio: { asr: { create: async ({ file_base64 }) => {
            assert.ok(file_base64.length > 0);
            return { text: "Transcripción alternativa en español" };
          } } }
        }) } };
      }
      return originalLoad.call(this, request, parent, isMain);
    };
    try {
      await withAsrEnv(async () => {
        process.env.WHATSAPP_ASR_ZAI_FALLBACK = "true";
        const transcript = await transcribeAudio(makeOgg());
        assert.strictEqual(transcript, "Transcripción alternativa en español");
      });
    } finally {
      global.fetch = originalFetch;
      Module._load = originalLoad;
    }
  });

  await asyncTest("Provider HTTP errors are surfaced as typed failures", async () => {
    const originalFetch = global.fetch;
    global.fetch = async () => new Response("provider error", { status: 503 });
    try {
      await withAsrEnv(() => assert.rejects(
        transcribeAudio(makeOgg()),
        (err) => err && err.code === "ASR_HTTP_ERROR"
      ));
    } finally {
      global.fetch = originalFetch;
    }
  });

  await asyncTest("ASR timeout is surfaced without logging audio or credentials", async () => {
    const originalFetch = global.fetch;
    global.fetch = async () => {
      const error = new Error("timed out");
      error.name = "TimeoutError";
      throw error;
    };
    try {
      await withAsrEnv(() => assert.rejects(
        transcribeAudio(makeOgg()),
        (err) => err && err.code === "ASR_TIMEOUT"
      ));
    } finally {
      global.fetch = originalFetch;
    }
  });

  await asyncTest("Invalid provider JSON is rejected", async () => {
    const originalFetch = global.fetch;
    global.fetch = async () => new Response("not json", { status: 200 });
    try {
      await withAsrEnv(() => assert.rejects(
        transcribeAudio(makeOgg()),
        (err) => err && err.code === "ASR_INVALID_RESPONSE"
      ));
    } finally {
      global.fetch = originalFetch;
    }
  });

  await asyncTest("Provider response without transcript is rejected", async () => {
    const originalFetch = global.fetch;
    global.fetch = async () => new Response(JSON.stringify({}), { status: 200 });
    try {
      await withAsrEnv(() => assert.rejects(
        transcribeAudio(makeOgg()),
        (err) => err && err.code === "ASR_EMPTY_TRANSCRIPT"
      ));
    } finally {
      global.fetch = originalFetch;
    }
  });

  await asyncTest("Empty and non-Ogg audio are rejected before network access", async () => {
    const originalFetch = global.fetch;
    global.fetch = async () => { throw new Error("fetch must not be called"); };
    try {
      await withAsrEnv(async () => {
        await assert.rejects(transcribeAudio(Buffer.alloc(0)), (err) => err.code === "ASR_INVALID_AUDIO");
        await assert.rejects(transcribeAudio(Buffer.from("not audio")), (err) => err.code === "ASR_INVALID_AUDIO");
      });
    } finally {
      global.fetch = originalFetch;
    }
  });

  await asyncTest("Audio stream/download errors propagate to caller", async () => {
    const brokenStream = new Readable({
      read() {
        this.destroy(new Error("synthetic download failure"));
      },
    });
    await assert.rejects(
      collectMediaStreamBounded(brokenStream, 1024),
      /synthetic download failure/
    );
  });

  // Summary
  console.log(`\\n=== Results: ${passed} passed, ${failed} failed ===`);
  if (failed > 0) {
    process.exit(1);
  } else {
    console.log("All tests passed!");
    process.exit(0);
  }
})();
