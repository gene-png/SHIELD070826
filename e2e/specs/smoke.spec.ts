import { test, expect, request } from "@playwright/test";

// Sprint 0 gate. These assertions exist to prove the harness itself works:
// that Playwright runs, that it can reach the web app over the compose
// network, and that the API behind it is alive. They deliberately assert on
// real anchors (the API's liveness contract and the homepage <h1>) rather
// than on a bare 200, so a blank-but-serving app cannot pass.

const API_BASE = process.env.PLAYWRIGHT_API_URL ?? "http://api:8000";

test("api liveness probe reports ok", async () => {
  const ctx = await request.newContext();
  const res = await ctx.get(`${API_BASE}/health`);
  expect(res.status()).toBe(200);
  expect(await res.json()).toMatchObject({ status: "ok" });
  await ctx.dispose();
});

test("homepage renders its headline", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: /Endless Possibilities/i }),
  ).toBeVisible();
});

test("page loads without console errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  expect(errors).toEqual([]);
});
