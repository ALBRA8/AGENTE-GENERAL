import { expect, test } from "@playwright/test";

const config = {
  provider: "mock",
  base_url: "",
  model: "",
  temperature: 0.5,
  top_p: 1,
  max_tokens: 1024,
  timeout: 60,
  stream: false,
  api_key_set: false,
  last_test_at: null,
  last_test_ok: null,
};

test("requires an admin token before connecting and keeps it out of browser storage", async ({ page }) => {
  const seenAuthorization: string[] = [];
  await page.route("**/api/v1/provider/config", async (route) => {
    seenAuthorization.push(route.request().headers()["authorization"] ?? "");
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(config) });
  });
  await page.route("**/api/v1/provider/defaults", async (route) => {
    seenAuthorization.push(route.request().headers()["authorization"] ?? "");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ mock: {}, nvidia: { base_url: "https://integrate.api.nvidia.com/v1", model: "z-ai/glm-5.3", temperature: 0.5, top_p: 1, max_tokens: 1024, timeout: 60, stream: false } }),
    });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Configuración.*Proveedor de IA/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Conectar" })).toBeDisabled();

  await page.getByLabel("Token de acceso del administrador").fill("test-admin-token-not-a-real-secret");
  await page.getByRole("button", { name: "Conectar" }).click();

  await expect(page.getByText("Mock (sin configurar)")).toBeVisible();
  expect(seenAuthorization).toHaveLength(2);
  expect(seenAuthorization.every((value) => value === "Bearer test-admin-token-not-a-real-secret")).toBe(true);
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }));
  expect(stored).not.toContain("test-admin-token-not-a-real-secret");
});

test("shows API authorization failures instead of treating them as successful configuration", async ({ page }) => {
  await page.route("**/api/v1/provider/config", async (route) => {
    await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Unauthorized" }) });
  });
  await page.route("**/api/v1/provider/defaults", async (route) => {
    await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Unauthorized" }) });
  });

  await page.goto("/");
  await page.getByLabel("Token de acceso del administrador").fill("invalid-token");
  await page.getByRole("button", { name: "Conectar" }).click();
  await expect(page.getByText("HTTP 401")).toBeVisible();
  await expect(page.getByText("Mock (sin configurar)")).toHaveCount(0);
});
