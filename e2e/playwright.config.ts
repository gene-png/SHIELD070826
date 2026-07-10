import { defineConfig, devices } from "@playwright/test";

// Inside the compose network the web app is reachable as `web:3000`; from a
// developer's host it is `localhost:3000`. PLAYWRIGHT_BASE_URL lets the same
// config serve both without a second file.
//
// IMPORTANT: the web service's NEXTAUTH_URL must equal this origin, or NextAuth
// rejects the credentials callback (host-mismatch CSRF) and no session cookie is
// ever set — every sign-in silently fails. For the in-network default
// (http://web:3000) bring web up with `NEXTAUTH_URL=http://web:3000` (see
// e2e/README.md). The compose default is http://localhost:3000; do not change
// that default (it is correct for a human on the host).
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://web:3000";

export default defineConfig({
  testDir: "./specs",
  // The API boots by running `alembic upgrade head`, and the web container may
  // still be doing its first `pnpm install`. Both are slow on a cold stack.
  // The web tier runs `next dev`, so the FIRST hit to each route pays a
  // just-in-time compile; the multi-context client-thread flow (two sign-ins,
  // an engagement, a workspace, a reply) can cross 60s entirely on cold
  // compiles + module imports the first time through. 120s absorbs that without
  // masking a real hang (a genuinely stuck call still fails at the action
  // timeout well before this).
  timeout: 120_000,
  // Same next-dev JIT reason as the test timeout above, one level down: an
  // assertion whose FIRST hit to a route triggers that route's cold compile can
  // exceed the 15s Playwright default. CI brings up a fresh stack every run, so
  // every route is cold on first touch — the CSF workspace render and the
  // multi-artifact Playbook export were the two that flaked at 15s. 30s absorbs
  // the compile without hiding a hang; individual heavy assertions raise it
  // further inline (see the export in playbook-export-gate).
  expect: { timeout: 30_000 },
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
