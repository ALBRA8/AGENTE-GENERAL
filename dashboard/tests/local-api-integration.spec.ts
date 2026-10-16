import { expect, test } from "@playwright/test";

// This test deliberately does not mock API routes. The dedicated CI workflow
// starts the real ASGI app on loopback and builds the dashboard against it.
test("real local API accepts the admin token through browser CORS", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Configuración.*Proveedor de IA/ })).toBeVisible();

  await page.getByLabel("Token de acceso del administrador").fill(
    "ci-local-provider-settings-token-32chars",
  );
  await page.getByRole("button", { name: "Conectar" }).click();

  await expect(page.getByText("Mock (sin configurar)")).toBeVisible();
  await expect(page.getByText("HTTP 401")).toHaveCount(0);
  await expect(page.getByText(/Failed to fetch|NetworkError/i)).toHaveCount(0);

  // The token is intentionally held in component memory, not persisted.
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(stored).not.toContain("ci-local-provider-settings-token-32chars");
});
