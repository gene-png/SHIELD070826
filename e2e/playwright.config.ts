import { defineConfig, devices } from "@playwright/test";

// Inside the compose network the web app is reachable as `web:3000`; from a
// developer's host it is `localhost:3000`. PLAYWRIGHT_BASE_URL lets the same
// config serve both without a second file.
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://web:3000";

export default defineConfig({
  testDir: "./specs",
  // The API boots by running `alembic upgrade head`, and the web container may
  // still be doing its first `pnpm install`. Both are slow on a cold stack.
  timeout: 60_000,
  expect: { timeout: 15_000 },
  // A flaky e2e suite is worse than none: it trains people to ignore red.
  // Retry once so a genuinely flaky test is visible as `flaky`, not as `passed`.
  retries: process.env.CI ? 2 : 1,
  // Parallel workers share one Postgres and one seeded tenant. Serialise until
  // the specs are provably independent.
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
    ["json", { outputFile: "test-results/results.json" }],
  ],
  outputDir: "./test-results",
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
