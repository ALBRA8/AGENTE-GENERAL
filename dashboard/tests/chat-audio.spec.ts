import { expect, test } from "@playwright/test";

test("chat audio fails closed when the ASR endpoint is not configured", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: [{ role: "user", content: "Transcribe this audio" }],
        audio: "data:audio/ogg;base64,T2dnUw==",
      }),
    });
    return { status: response.status, body: await response.json() };
  });

  expect(result.status).toBe(503);
  expect(result.body.error).toBe("Audio transcription is not configured");
});

test("chat audio rejects malformed media data before contacting a provider", async ({ page }) => {
  await page.goto("/");
  const result = await page.evaluate(async () => {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        messages: [{ role: "user", content: "Transcribe this audio" }],
        audio: "not-a-data-url",
      }),
    });
    return { status: response.status, body: await response.json() };
  });

  expect(result.status).toBe(400);
  expect(result.body.error).toBe("Invalid audio format");
});
